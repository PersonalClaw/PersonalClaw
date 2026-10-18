"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_terminal_persist_capability.py``

Its checks:
``tests/rails/test_terminal_persist_capability_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

import time

import pytest

from personalclaw.dashboard.handlers import terminal
from tests.test_terminal_handler import _make_request  # noqa: F401  (shared request builder)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """The panel must be on for the list handler to answer at all."""
    terminal._enabled_cache[0] = True
    terminal._enabled_cache[1] = time.monotonic()
    yield
    terminal._enabled_cache[0] = False
    terminal._enabled_cache[1] = 0.0
