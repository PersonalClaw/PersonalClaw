"""The frozen bundle runs the package's own child modules the way ``python -m`` runs them.

Wherever PersonalClaw runs from an interpreter, the gateway starts some of its children as
``<interpreter> -m <module> …``: the resource-ceiling shim in front of every tool command, MCP
server, app backend and worker it starts (``sandbox.spawn_shim_argv``), the computer-use driver
(``computer_use.service``) and an evaluation cell (``evals.runner``). In the desktop app that
interpreter is the frozen bundle's own executable, and its entry script is the CLI, so each of
those commands reached the CLI's parser and was refused at once.

So the bundle's entry script (``personalclaw/__main__.py``) asks :func:`child_module` before it
starts the CLI. A frozen process whose command line is ``-m <module> …``, naming one of
:data:`CHILD_MODULES`, runs that module as ``python -m`` would: as ``__main__``, with
``sys.argv`` holding the module's path and then the arguments after it. The CLI is never
imported, and the entry script's own set-up does not run, so the child starts as it would from an
interpreter. Every other command line is the CLI's, whose parser decides as it always has: a
module that is not declared here is refused there. Only the declared modules run this way; the
bundle is not an interpreter for any other code.

First of all, the bundle gives back the environment it was started with
(:func:`restore_environment`): what its own start-up added is taken out again, so a child module
hands the programs it runs the environment the gateway built for them, and a restart starts its new
image the way the desktop shell started the first.

A pure-stdlib leaf, like the shim it serves: it runs before the rest of the package, on every
spawn the shim fronts. ``scripts/backend_bundle_manifest.py`` reads :data:`CHILD_MODULES` from
this file without importing it, so the bundle carries each module: nothing imports them by name.
"""

from __future__ import annotations

import os
import sys
from typing import NoReturn

#: The prefix of every variable the bundle's bootloader sets for itself before any Python runs:
#: its archive, its application folder, how deep in a chain of its own processes this one is. A
#: program started with them still set reads as a part of that chain.
BOOTLOADER_PREFIX = "_PYI_"

#: Every module the gateway starts as ``<interpreter> -m <module>``. A frozen bundle runs exactly
#: these (and ``tests/test_python_children_in_the_desktop_app.py`` holds each call site to
#: this list).
CHILD_MODULES: tuple[str, ...] = (
    "personalclaw._spawn_exec_shim",
    "personalclaw.computer_use.driver_host",
    "personalclaw.evals.child",
)


def restore_environment() -> None:
    """Take back out of this process's environment what the bundle's own start-up put in.

    The bootloader's ``_PYI_…`` variables were handed on to every program the shim started, past
    the environment the gateway had built for it without them, and to the image a restart started.
    On Linux the bootloader also puts the bundle's folder on the library path and keeps the value
    it replaced in ``LD_LIBRARY_PATH_ORIG``: that value goes back, and a library path that is only
    the bundle's own folder goes, so a system program never loads the bundle's libraries.
    """
    for name in [name for name in os.environ if name.startswith(BOOTLOADER_PREFIX)]:
        del os.environ[name]
    if sys.platform.startswith("linux"):
        replaced = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if replaced is not None:
            os.environ["LD_LIBRARY_PATH"] = replaced
        else:
            bundle = getattr(sys, "_MEIPASS", None)
            if bundle and os.environ.get("LD_LIBRARY_PATH") == bundle:
                del os.environ["LD_LIBRARY_PATH"]


def child_module(argv: list[str]) -> str | None:
    """The declared module *argv* asks to run (``<bundle> -m <module> …``), or ``None``."""
    if len(argv) >= 3 and argv[1] == "-m" and argv[2] in CHILD_MODULES:
        return argv[2]
    return None


def run(module: str) -> NoReturn:
    """Run *module* as ``python -m <module>`` runs it, and end the process as that would.

    ``sys.argv`` loses the ``-m <module>`` pair, and ``runpy`` sets its first item to the module's
    path, so the module reads the same argv it would read under an interpreter. A module that
    returns ends the process with status 0, one that raises ``SystemExit`` with its status.
    """
    import runpy

    del sys.argv[1:3]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    raise SystemExit(0)
