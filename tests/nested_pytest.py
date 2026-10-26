"""The one way a test starts a pytest of its own: with a base temporary folder of its own.

Every pytest session, as it ends, deletes each numbered session in the shared
``pytest-of-<user>`` root that is more than three behind the newest and no longer locked. A
nested pytest that used that root could end by deleting an outer session's finished tree, one
unlink at a time: breadth's random-order leg ran two sessions in one job, and a nested pytest in
the second spent the suite's whole 30 s deleting the first's 14k tests (run 40199713134). Given
``--basetemp`` in the test's own folder, a nested pytest makes and cleans only its own.

:func:`argv` is the command, and the rail
``tests/rails/test_a_nested_pytest_keeps_a_basetemp_of_its_own_tree.py`` holds every nested
pytest in ``tests/`` to it.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The folder, under the one a test gives, that a nested pytest takes as its base temporary folder.
FOLDER = "nested-basetemp"


def basetemp(where: str | Path) -> str:
    """The flag that gives a nested pytest ``<where>/nested-basetemp`` as its own basetemp. pytest
    makes it, and empties it first if it is there."""
    return f"--basetemp={Path(where) / FOLDER}"


def argv(where: str | Path, *args: str, python: str = sys.executable) -> list[str]:
    """``python -m pytest`` with a basetemp of its own under *where* (the test's ``tmp_path``, or
    a folder it owns), then *args*."""
    return [python, "-m", "pytest", basetemp(where), *args]
