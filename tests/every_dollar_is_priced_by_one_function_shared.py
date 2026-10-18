"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_dollar_is_priced_by_one_function.py``

Its checks:
``tests/rails/test_every_dollar_is_priced_by_one_function_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.routing import rates as rates_mod


@pytest.fixture(autouse=True)
def _clear_rate_caches():
    """The overlay memo is process-global (stat-keyed); reset it around each test."""
    rates_mod._overlay_cache = None
    yield
    rates_mod._overlay_cache = None
