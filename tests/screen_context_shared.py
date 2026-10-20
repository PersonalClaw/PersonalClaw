"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_screen_context.py``

Its checks:
``tests/rails/test_screen_context_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

import pytest

from personalclaw.dashboard import screen_context


@pytest.fixture(autouse=True)
def _clean_slots():
    """No slot leaks between tests — the registry is process-global by design."""
    screen_context.clear_all()
    yield
    screen_context.clear_all()
