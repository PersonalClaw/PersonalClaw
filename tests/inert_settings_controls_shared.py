"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_inert_settings_controls.py``

Its checks:
``tests/rails/test_inert_settings_controls_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


#: The four leaves deleted by this change, each with the dataclass that used to declare it.
DELETED: tuple[tuple[str, str, str], ...] = (
    ("ambient.surfaces_max_layer", "AmbientConfig", "surfaces_max_layer"),
    ("ambient.tray_enabled", "AmbientConfig", "tray_enabled"),
    ("sources.daily_request_budget", "SourcesConfig", "daily_request_budget"),
    ("packs.connector_catalog_url", "PacksConfig", "connector_catalog_url"),
)
