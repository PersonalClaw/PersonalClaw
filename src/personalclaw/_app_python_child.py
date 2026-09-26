"""Run an app's Python entry script with the app packages loaded AFTER the interpreter's own.

``python -m personalclaw._app_python_child <script.py> [args...]``

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
``sys.argv[0]`` is the script and ``sys.path[0]`` is its directory.

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
    # `python -m` put the working directory first; `python <script>` puts the script's own
    # directory there, which is what an entry script importing its siblings relies on.
    sys.path[0] = os.path.dirname(os.path.abspath(script))
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
