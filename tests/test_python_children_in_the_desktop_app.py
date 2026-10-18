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
* **one of the package's own child modules** through ``self_update.python_child_argv``
  (``<sys.executable> -P -m <module>``), which the bundle's entry script runs the way
  ``python -m`` does, for exactly the modules ``_frozen_child.CHILD_MODULES`` declares (and which
  the bundle therefore carries).

From an interpreter every one of them carries ``-P``: ``-m`` alone puts the child's working folder
first on its import path, so a folder holding a ``personalclaw`` package (a checkout's ``src/``,
any project with one at its top) ran its own code in place of this install's, and in the folder an
agent works in, ahead of the sandbox the ceiling shim then starts.

What is left needs a real Python interpreter, which the desktop app does not have, and is named
below with the reason and where the desktop app refuses it before it starts (``python_children``,
whose sentence each refusal says; ``test_the_desktop_app_refuses_python_children`` holds each
one to it), or why the desktop app never reaches it. The census is an AST walk over every list or
tuple whose first item is ``sys.executable``, and every call of ``python_child_argv``: a new one
reds :func:`test_every_interpreter_child_is_classified` until its author says which kind it is.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from python_children_in_the_desktop_app_shared import _REPO, _declared

from personalclaw import self_update
from personalclaw.cli import build_parser


def test_every_declared_module_is_a_module_of_the_package():
    missing = [module for module in _declared() if importlib.util.find_spec(module) is None]
    assert not missing, missing


def test_the_bundle_carries_every_declared_module():
    """Nothing imports them, so the bundle has them only because the spec asks for every module
    the package names (``backend_bundle_manifest.by_name_modules``), these among them."""
    spec = importlib.util.spec_from_file_location(
        "backend_bundle_manifest", _REPO / "scripts" / "backend_bundle_manifest.py"
    )
    assert spec and spec.loader
    manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest)
    missing = set(_declared()) - set(manifest.by_name_modules(_REPO))
    assert not missing, sorted(missing)
    assert "manifest.by_name_modules()" in (_REPO / "personalclaw-backend.spec").read_text(
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
    tmp_path: Path, *args: str, env: dict[str, str] | None = None, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run the package's entry script as the frozen bundle runs it: as ``__main__``, in a process
    that reads as frozen, with ``sys.executable`` first on its command line and *args* after it,
    and *stdin* on its standard input. A scratch home, and the shim's own environment otherwise,
    so nothing reaches the real one."""
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
        input=stdin,
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


def test_the_frozen_entry_runs_the_content_scan_child_like_python_dash_m(tmp_path):
    """Every upload, Knowledge write and artifact save waits for a scan's child: in the desktop
    app it is the bundle running the scan's module, and it answers the verdict on its stdout."""
    done = _frozen_entry(
        tmp_path,
        "-m",
        "personalclaw.uploads.scan_child",
        stdin="Shopping list for Saturday: eggs, flour, apples.\n",
    )
    assert (done.returncode, done.stdout) == (0, '{"dangerous": false}\n'), done.stderr


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


class TestAModuleChildStartsInTheBundleAndAnInterpreter:
    def test_the_bundle_runs_it_as_its_entry_reads_it(self, frozen):
        """The entry runs a declared child only when ``-m`` is its first word
        (``_frozen_child.child_module``), and the bootloader puts no working folder on the path."""
        from personalclaw import _frozen_child

        argv = self_update.python_child_argv("personalclaw._spawn_exec_shim", "{}", "--", "x")
        assert argv == [frozen, "-m", "personalclaw._spawn_exec_shim", "{}", "--", "x"]
        assert _frozen_child.child_module(argv) == "personalclaw._spawn_exec_shim"

    def test_an_interpreter_runs_it_without_its_working_folder(self):
        argv = self_update.python_child_argv("personalclaw.evals.child", "cell.json")
        assert argv == [sys.executable, "-P", "-m", "personalclaw.evals.child", "cell.json"]


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
        assert self_update.cli_argv() == [sys.executable, "-P", "-m", "personalclaw"]

    def test_the_availability_probe(self, frozen):
        from personalclaw.providers import availability

        assert _accepted(availability.probe_argv(["some-app"]), frozen) == "availability-probe"

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
