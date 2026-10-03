"""No test drives this machine's container runtime unless the run opted in.

Two halves (``tests/container_runtime.py`` is the mechanism):

* **A census of the tree.** Every test module is read for the shapes that drive a container runtime
  wherever its program happens to be on ``PATH``: looking one up itself (``shutil.which("docker")``,
  or the same over a list of such names), starting one by name (``subprocess.run(["docker", ...])``,
  a shell command that starts with one), and a sandbox provider's probe that runs the program as it
  answers (``docker_available``, ``lima_available``). Each must ask the opt-in first: a lookup only
  through ``container_runtime``, a start or a probe only in a test or fixture that asks
  ``container_runtime.require()`` or ``available()``, and a probe as a module is imported only after
  ``container_runtime.available(...) and``. CI sets the opt-in, so on CI the guard below refuses
  nothing: this census is what catches a new test there.
* **The guard, driven.** The machine's own runtime, started without the opt-in, is refused before it
  starts and named; a stand-in a test wrote starts; with the opt-in the machine's own starts too.
  And the four modules that drive a runtime skip, saying how to run them, with every runtime they
  look for on ``PATH`` and the opt-in unset.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import container_runtime
import pytest

TESTS = Path(__file__).resolve().parent
_OWN = {"container_runtime.py", Path(__file__).name}

#: The calls that start a process, by the name they are called through.
_STARTERS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "system",
        "popen",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execlp",
        "spawnv",
        "spawnvp",
        "spawnl",
        "spawnlp",
        "posix_spawn",
        "posix_spawnp",
    }
)
#: The sandbox providers' probes, which run the runtime's program when it is on ``PATH``.
_PROBES = frozenset({"docker_available", "lima_available", "lima_unavailable_reason"})
#: The ``container_runtime`` calls that ask the opt-in.
_ASKS = frozenset({"require", "available", "opted_in"})


def _name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _asks(node: ast.AST) -> bool:
    """Whether *node* contains a ``container_runtime.require/available/opted_in`` call."""
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in _ASKS
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "container_runtime"
        for n in ast.walk(node)
    )


def _runtime_word(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and os.path.basename(node.value.split(" ", 1)[0]) in container_runtime.PROGRAMS
    )


class _Reader(ast.NodeVisitor):
    """Every shape in one module that drives a runtime without asking, as ``line: what``."""

    def __init__(self, tree: ast.Module) -> None:
        self.findings: list[str] = []
        self.asked: list[int] = []
        self._scopes: list[ast.AST] = []
        # Names that hold a runtime's name: a loop over runtime names, a constant list of them,
        # a value `container_runtime` answered; and names that hold a command that starts one.
        self.runtime_names: set[str] = set()
        self.runtime_commands: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
                if any(_runtime_word(e) for e in node.value.elts):
                    self.runtime_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
                if self._names_runtimes(node.iter):
                    self.runtime_names.add(node.target.id)
            if isinstance(node, ast.Assign) and _asks(node.value):
                self.runtime_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and self._starts_a_runtime(node.value):
                self.runtime_commands |= {t.id for t in node.targets if isinstance(t, ast.Name)}

    def _names_runtimes(self, node: ast.AST) -> bool:
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(_runtime_word(e) for e in node.elts)
        return isinstance(node, ast.Name) and node.id in self.runtime_names

    def _is_runtime(self, node: ast.AST) -> bool:
        return _runtime_word(node) or (isinstance(node, ast.Name) and node.id in self.runtime_names)

    def _starts_a_runtime(self, node: ast.AST) -> bool:
        """Whether *node* is a command line whose program is a runtime."""
        if isinstance(node, (ast.List, ast.Tuple)):
            return bool(node.elts) and self._is_runtime(node.elts[0])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._starts_a_runtime(node.left)
        if isinstance(node, ast.Name):
            return node.id in self.runtime_commands
        return _runtime_word(node)

    def _in_a_scope_that_asks(self) -> bool:
        return any(_asks(scope) for scope in self._scopes)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 — ast visitor
        self._scopes.append(node)
        self.generic_visit(node)
        self._scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802 — ast visitor
        # `container_runtime.available(...) and probe()` asks before the probe can run.
        if isinstance(node.op, ast.And):
            for i, value in enumerate(node.values):
                asked_before = ast.BoolOp(op=ast.And(), values=node.values[:i])
                if _asks(asked_before):
                    self._scopes.append(asked_before)
                    self.visit(value)
                    self._scopes.pop()
                else:
                    self.visit(value)
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 — ast visitor
        name = _name(node)
        if name == "which" and node.args and self._is_runtime(node.args[0]):
            self.findings.append(
                f"{node.lineno}: looks a container runtime up on PATH itself; ask "
                "container_runtime.require() or available(), which ask the opt-in too"
            )
        elif name in _STARTERS and node.args and self._starts_a_runtime(node.args[0]):
            if self._in_a_scope_that_asks():
                self.asked.append(node.lineno)
            else:
                self.findings.append(
                    f"{node.lineno}: starts a container runtime without asking "
                    "container_runtime.require() first"
                )
        elif name in _PROBES:
            if self._in_a_scope_that_asks():
                self.asked.append(node.lineno)
            else:
                self.findings.append(
                    f"{node.lineno}: {name}() runs the runtime's program when it is on PATH; ask "
                    "container_runtime.available() first"
                )
        self.generic_visit(node)


def _read(source: str) -> _Reader:
    tree = ast.parse(source)
    reader = _Reader(tree)
    reader.visit(tree)
    return reader


def _modules() -> list[Path]:
    return sorted(p for p in TESTS.rglob("*.py") if p.name not in _OWN)


def test_no_test_drives_a_container_runtime_without_asking_the_opt_in():
    found = {
        str(path.relative_to(TESTS.parent)): reader.findings
        for path in _modules()
        if (reader := _read(path.read_text(encoding="utf-8"))).findings
    }
    assert not found, (
        "these drive a container runtime wherever its program is on PATH, without the run's "
        f"opt-in ({container_runtime.OPT_IN}=1): {found}"
    )


def test_the_census_sees_the_modules_that_drive_a_runtime():
    """The control: each module that drives a runtime is found asking, so a census that read
    nothing, or stopped recognising a start, cannot pass by finding nothing."""
    asking = {
        path.name: reader.asked
        for path in _modules()
        if (reader := _read(path.read_text(encoding="utf-8"))).asked
    }
    for module in (
        "test_compose_standalone.py",
        "test_deployment_parity.py",
        "test_sandbox_docker.py",
        "test_sandbox_lima.py",
    ):
        assert module in asking, f"{module} no longer reads as starting a runtime: {asking}"


#: The shapes each module had when a sweep of the suite rebuilt images on a shared container VM.
_THE_OLD_SHAPES = {
    "a fixture looks a runtime up and composes with it": """
        def _container_runtime():
            for rt in ("docker", "finch"):
                if shutil.which(rt):
                    return rt

        def compose_gateway():
            runtime = _container_runtime()
            subprocess.run([runtime, "compose", "up", "-d", "--build"], check=True)
    """,
    "a probe tries each runtime it finds": """
        def _usable_runtime(scratch):
            for candidate in ("docker", "finch"):
                if not shutil.which(candidate):
                    continue
                subprocess.run([candidate, "compose", "-f", "control.yaml", "config"])
    """,
    "an import-time probe of the daemon": """
        _HAS_DOCKER = shutil.which("docker") is not None and docker_available(refresh=True)
    """,
    "an import-time probe of the VM": """
        _HAS_LIMA = shutil.which("limactl") is not None and lima_available(refresh=True)
    """,
    "a fixture that pulls an image": """
        def _sandbox_image_pulled():
            subprocess.run(["docker", "pull", sandbox_image()], check=False)
    """,
    "a shell command that runs a container": """
        def test_it():
            os.system("docker run --rm busybox true")
    """,
}


@pytest.mark.parametrize("shape", sorted(_THE_OLD_SHAPES))
def test_the_census_reds_on_the_shapes_that_drove_the_runtime(shape):
    assert _read(textwrap.dedent(_THE_OLD_SHAPES[shape])).findings, shape


def test_the_asking_shapes_read_clean():
    asking = textwrap.dedent("""
        _HAS_DOCKER = bool(container_runtime.available("docker")) and docker_available(refresh=True)

        def compose_gateway():
            runtime = container_runtime.require("docker", "finch")
            args = [runtime, "compose", "-f", "compose.yaml"]
            subprocess.run(args + ["up", "-d", "--build"], check=True)

        def _usable_runtime(scratch):
            for candidate in ("docker", "finch"):
                if not container_runtime.available(candidate):
                    continue
                subprocess.run([candidate, "compose", "config"])
    """)
    reader = _read(asking)
    assert reader.findings == []
    assert len(reader.asked) == 3, reader.asked


# ── the guard ─────────────────────────────────────────────────────────────────────────────────


def _stand_in(folder: Path, name: str, log: Path) -> Path:
    """A program called *name* that records that it ran, and exits 1."""
    folder.mkdir(parents=True, exist_ok=True)
    program = folder / name
    program.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 1\n', encoding="utf-8")
    program.chmod(0o755)
    return program


def test_the_machines_own_runtime_is_refused_before_it_starts(tmp_path, unset_env):
    """A guard that counts no folder as a test's own reads every runtime as the machine's: the
    launch raises before the program starts, and the refusal names the test and the program."""
    unset_env(container_runtime.OPT_IN)
    log = tmp_path / "ran.log"
    program = _stand_in(tmp_path / "bin", "docker", log)
    guard = container_runtime.Guard(own=[])
    guard.install()
    try:
        with pytest.raises(PermissionError, match="this machine's own"):
            subprocess.run([str(program), "build", "."], check=False)
    finally:
        guard.undo()
    refused = guard.take()
    assert len(refused) == 1 and "docker build ." in refused[0], refused
    assert "test_the_machines_own_runtime_is_refused_before_it_starts" in refused[0], refused
    assert not log.exists(), "the program started before the guard refused it"


def test_with_the_opt_in_the_machines_runtime_starts(tmp_path, monkeypatch):
    monkeypatch.setenv(container_runtime.OPT_IN, "1")
    log = tmp_path / "ran.log"
    program = _stand_in(tmp_path / "bin", "finch", log)
    guard = container_runtime.Guard(own=[])
    guard.install()
    try:
        subprocess.run([str(program), "version"], check=False)
    finally:
        guard.undo()
    assert guard.take() == []
    assert log.read_text(encoding="utf-8").split() == ["finch", "version"]


def test_a_stand_in_a_test_wrote_starts(tmp_path, monkeypatch, unset_env):
    """The suite's own guard counts the temporary folders as a test's: a program a test wrote there
    to stand for a runtime starts, found on PATH by name."""
    unset_env(container_runtime.OPT_IN)
    log = tmp_path / "ran.log"
    folder = tmp_path / "bin"
    _stand_in(folder, "docker", log)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")

    subprocess.run(["docker", "version"], check=False)

    assert log.read_text(encoding="utf-8").split() == ["docker", "version"]


def test_require_skips_and_says_how_to_run_it(tmp_path, monkeypatch, unset_env):
    unset_env(container_runtime.OPT_IN)
    folder = tmp_path / "bin"
    _stand_in(folder, "finch", tmp_path / "ran.log")
    monkeypatch.setenv("PATH", str(folder))

    with pytest.raises(pytest.skip.Exception) as skipped:
        container_runtime.require("docker", "finch")
    reason = str(skipped.value)
    assert container_runtime.SKIP_PREFIX in reason and "finch is on PATH" in reason, reason
    assert f"{container_runtime.OPT_IN}=1" in reason, reason
    assert container_runtime.available("docker", "finch") == ""

    monkeypatch.setenv(container_runtime.OPT_IN, "1")
    assert container_runtime.require("docker", "finch") == "finch"
    assert container_runtime.available("docker", "finch") == "finch"

    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    with pytest.raises(pytest.skip.Exception, match="no docker or finch on PATH"):
        container_runtime.require("docker", "finch")


def test_the_runs_summary_lists_the_skips():
    def skipped(nodeid: str, reason: str) -> SimpleNamespace:
        return SimpleNamespace(nodeid=nodeid, longrepr=("file.py", 1, f"Skipped: {reason}"))

    reports = [
        skipped("tests/a.py::test_build", f"{container_runtime.SKIP_PREFIX} (finch is on PATH)"),
        skipped("tests/b.py::test_parse", "no tree-sitter grammar for python"),
    ]
    assert container_runtime.skipped_without_the_opt_in(reports) == ["tests/a.py::test_build"]


def test_a_module_whose_import_started_a_runtime_fails_its_collection():
    plugin = container_runtime.Plugin()
    wrapper = plugin.pytest_make_collect_report(collector=None)
    next(wrapper)
    with container_runtime.GUARD._lock:
        container_runtime.GUARD._refused.append("(no test) -> docker version (docker is ...)")
    report = SimpleNamespace(outcome="passed", longrepr=None)
    with pytest.raises(StopIteration) as done:
        wrapper.send(report)
    assert done.value.value.outcome == "failed"
    assert "docker version" in done.value.value.longrepr


# ── the modules that drive a runtime, driven ───────────────────────────────────────────────────

#: The tests that drive a runtime, run here with a stand-in for every runtime they look for. The
#: Compose parity module is not among them: run on a machine with a runtime and the opt-in it builds
#: two images, and its own two tests hold its fixture's shape.
_GATED = (
    "tests/test_compose_standalone.py::test_compose_config_succeeds_from_a_scratch_directory",
    "tests/test_sandbox_docker.py::test_container_runs_uid_aligned",
    "tests/test_sandbox_docker.py::test_host_path_outside_workspace_is_not_visible",
    "tests/test_sandbox_lima.py::test_guest_runs_with_translated_workdir",
)


@pytest.mark.timeout(300)
def test_the_tests_that_drive_a_runtime_skip_without_the_opt_in(tmp_path):
    """With docker, finch and limactl all on PATH and the opt-in unset, each test that drives one is
    skipped with the reason that says how to run it, and none of the programs ran: not as a module
    was imported, and not in a test."""
    log = tmp_path / "ran.log"
    folder = tmp_path / "bin"
    for name in ("docker", "finch", "limactl"):
        _stand_in(folder, name, log)
    env = {
        k: v
        for k, v in os.environ.items()
        if k != container_runtime.OPT_IN and not k.startswith(("PYTEST_", "COV_"))
    }
    env["PATH"] = os.pathsep.join(
        [str(folder), os.path.dirname(sys.executable), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    )
    report = tmp_path / "report.xml"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-n0", "-q", "--no-cov", "--color=no"]
        + [f"--junitxml={report}", *_GATED],
        cwd=TESTS.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=280,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    cases = ElementTree.parse(report).getroot().iter("testcase")
    skips = {f"{case.get('classname')}::{case.get('name')}": case.find("skipped") for case in cases}
    assert len(skips) == len(_GATED), skips
    for test, skip in skips.items():
        assert skip is not None, f"{test} ran"
        assert container_runtime.SKIP_PREFIX in skip.get("message", ""), (test, skip.attrib)
        assert f"{container_runtime.OPT_IN}=1" in skip.get("message", ""), (test, skip.attrib)
    assert not log.exists(), f"a runtime ran: {log.read_text(encoding='utf-8')}"
