"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_companion_discovery.py``

Its checks:
``tests/rails/test_companion_discovery_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from unittest.mock import patch

import pytest

from personalclaw.companion import discovery as disc


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    """Point config_dir/config_path at a temp home and reset the module's process state.

    ``discovery`` holds a process-global advertiser (the gateway has exactly one), so a test
    that left one running would leak a live socket — and an announcement — into the next.
    """
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.config.loader.config_path", return_value=cfg),
    ):
        disc.shutdown()
        yield tmp_path
        disc.shutdown()
