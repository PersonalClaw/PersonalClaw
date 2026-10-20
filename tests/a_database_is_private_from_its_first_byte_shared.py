"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_database_is_private_from_its_first_byte.py``

Its checks:
``tests/rails/test_a_database_is_private_from_its_first_byte_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

USUAL_UMASK = 0o022


@pytest.fixture(autouse=True)
def _the_usual_umask() -> Iterator[None]:
    """The umask most accounts run with, which leaves a new file readable by everyone."""
    previous = os.umask(USUAL_UMASK)
    try:
        yield
    finally:
        os.umask(previous)
