"""Run an app's Python entry script with the app packages loaded AFTER the interpreter's own.

``python -P -m personalclaw._app_python_child <script.py> [args...]``

The packages apps declare live in ``<home>/app-python`` (``apps/app_python.py``), and the gateway
loads them by APPENDING that directory to ``sys.path``: whatever the interpreter already provides
always wins the import, so an app can add a module but never replace one the gateway uses. A child
the gateway starts for an app — its backend, its background worker — needs the same packages with
the same precedence, and ``PYTHONPATH`` cannot give it that: its entries come BEFORE the standard
library and site-packages. Those children are started through the resource-ceiling shim
(``python -m personalclaw._spawn_exec_shim``), so an app package on ``PYTHONPATH`` named
``resource`` would shadow the very module the shim limits the child with.

So the child runs this module instead of the script: it appends every directory named in
``PERSONALCLAW_APP_PYTHON_PATH`` with :func:`site.addsitedir` (processing ``.pth`` files, exactly
as the gateway does), then runs the script as ``__main__`` the way ``python <script.py>`` would —
``sys.argv[0]`` is the script and ``sys.path[0]`` is its directory. The child starts with ``-P``
(``self_update.python_child_argv``), so its path holds no working folder, and the script's
directory goes in front of the interpreter's own entries rather than in place of one; the frozen
bundle runs it in place with the path its bootloader set, and the same holds there.

Stdlib only and free of ``personalclaw`` imports, like ``_spawn_exec_shim``: it runs before any app
code, in a child that has not imported the rest of the package.
"""

from __future__ import annotations

import os
import runpy
import site
import sys

#: The directories to append, ``os.pathsep``-separated. Set by the launcher
#: (``app_python.child_env``); an absent or empty value appends nothing.
PATH_ENV = "PERSONALCLAW_APP_PYTHON_PATH"


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("usage: python -m personalclaw._app_python_child <script.py> [args...]\n")
        return 2
    for directory in os.environ.get(PATH_ENV, "").split(os.pathsep):
        if directory:
            site.addsitedir(directory)
    script = argv[0]
    sys.argv = list(argv)
    # `python <script>` puts the script's own directory first, which is what an entry script
    # importing its siblings relies on. Started with `-P`, or run in place by the frozen bundle,
    # this child has no working folder on its path: the first entry is the interpreter's or the
    # bundle's own, so the script's directory goes in front of it. Under a bare `-m` the first
    # entry is the working folder, and the script's directory takes its place.
    here = os.path.dirname(os.path.abspath(script))
    if sys.flags.safe_path or getattr(sys, "frozen", False):
        sys.path.insert(0, here)
    else:
        sys.path[0] = here
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
