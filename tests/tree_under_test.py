"""Every child a test starts imports ``personalclaw`` from the tree under test.

pytest imports ``personalclaw`` from this checkout's ``src`` (pyproject's ``pythonpath``, which
the provenance rail in ``tests/conftest.py`` holds it to), but a child process does not inherit
that path. It finds ``personalclaw`` through the venv's editable install, and a venv shared by
several clones points at one of them: on a Mac with several, a probe's child imported a clone
three days old, whose ``handlers_system`` had no ``_IDENTITY``, and died before it said anything.
``tests/conftest.py`` puts this tree's ``src`` first on the run's ``PYTHONPATH`` once, before any
test (:func:`put_first`), so every child a test starts with the run's environment inherits it. In
CI the venv installs from the checkout itself, so the entry names the tree it would import anyway.

A test that builds a child's ``PYTHONPATH`` from nothing does not inherit it, and calls
:func:`put_first` on that environment (``tests/fresh_interpreter.py``). Proof:
``tests/test_a_child_a_test_starts_imports_the_tree_under_test.py``.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def src() -> str:
    """The folder this process imported ``personalclaw`` from: the tree under test's ``src``."""
    import personalclaw

    return str(Path(personalclaw.__file__).resolve().parent.parent)


def put_first(environ: MutableMapping[str, str]) -> None:
    """Puts :func:`src` first on *environ*'s ``PYTHONPATH``, keeping the entries after it. An
    environment that has it first already is left as it is."""
    tree = src()
    rest = [one for one in environ.get("PYTHONPATH", "").split(os.pathsep) if one and one != tree]
    environ["PYTHONPATH"] = os.pathsep.join([tree, *rest])
