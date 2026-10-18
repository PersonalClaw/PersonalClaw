"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_routing_mute_reachable.py``

Its checks:
``tests/rails/test_routing_mute_reachable_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


ROUTE_TABLE = REPO / "src" / "personalclaw" / "dashboard" / "routes.py"


API_TS = REPO / "web" / "src" / "lib" / "api.ts"


_ROUTE_PREFIX = "/api/agents/routing/"


def _registered_routes() -> set[str]:
    """Every ``/api/agents/routing/*`` path the route table registers, from its own source."""
    src = ROUTE_TABLE.read_text()
    return set(re.findall(rf'add_(?:get|post)\(\s*"({re.escape(_ROUTE_PREFIX)}[a-z_]+)"', src))


def _api_ts_clients() -> dict[str, str]:
    """{route path: client name} for every ``api.ts`` entry naming a routing route.

    🪤 LINE-BASED, NOT ONE BIG REGEX. The first draft matched ``name: (…) => fn<T>('path')`` and
    silently missed ``routingStatus``, whose inline response type is an object literal — so the
    `>` characters inside ``get<{ enabled: boolean; … }>`` ended the match early. A scanner that
    misses the very client this rail exists for is worse than no scanner.
    """
    out: dict[str, str] = {}
    for line in API_TS.read_text().splitlines():
        path = re.search(rf"'({re.escape(_ROUTE_PREFIX)}[a-z_]+)'", line)
        name = re.match(r"\s*(\w+):", line)
        if path and name:
            out[path.group(1)] = name.group(1)
    return out
