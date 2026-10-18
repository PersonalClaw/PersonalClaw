"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_private_home_file_modes.py``

Its checks:
``tests/rails/test_private_home_file_modes_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)
