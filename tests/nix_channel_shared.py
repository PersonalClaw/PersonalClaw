"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_nix_channel.py``

Its checks:
``tests/rails/test_nix_channel_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
