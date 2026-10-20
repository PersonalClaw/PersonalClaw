"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_channel_delivery_per_provider.py``

Its checks:
``tests/rails/test_channel_delivery_per_provider_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw import channel_delivery as cd


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-level (like `inbox_providers.native_source`'s state hook), so a
    test that left a handle behind would leak into the next one's `owner_reachable` pick."""
    cd.register(None)
    yield
    cd.register(None)
