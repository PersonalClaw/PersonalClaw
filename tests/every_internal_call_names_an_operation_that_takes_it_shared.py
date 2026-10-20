"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_internal_call_names_an_operation_that_takes_it.py``

Its checks:
``tests/rails/test_every_internal_call_names_an_operation_that_takes_it_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from personalclaw.dashboard import server
from personalclaw.dashboard.token_auth import InternalRoute


def _routes() -> dict[str, InternalRoute]:
    return {
        entry: InternalRoute.parse(entry)
        for entry in server.INTERNAL_ROUTES | server.MIXED_INTERNAL_ROUTES
    }
