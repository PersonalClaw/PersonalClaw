"""The repository's root, for the rails in ``tests/rails/``.

A rail names the tree it reads from this one constant rather than counting ``parents`` from its
own file, so a rail that moves cannot quietly start reading the wrong folder. A module rather
than a ``conftest.py``: pytest imports every conftest under the one name ``conftest``, and a
second one would replace ``tests/conftest.py`` for the tests that import it by that name.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
