"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_deployed_page_runs_in_an_origin_of_its_own.py``

Its checks:
``tests/rails/test_a_deployed_page_runs_in_an_origin_of_its_own_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.dashboard.token_auth import revoke_all_sessions


@pytest.fixture(autouse=True)
def _no_sessions():
    revoke_all_sessions()
    yield
    revoke_all_sessions()
