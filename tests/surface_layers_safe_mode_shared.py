"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_surface_layers_safe_mode.py``

Its checks:
``tests/rails/test_surface_layers_safe_mode_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw import surface_layers


@pytest.fixture(autouse=True)
def _clean_latch():
    surface_layers.reset_for_tests()
    yield
    surface_layers.reset_for_tests()
