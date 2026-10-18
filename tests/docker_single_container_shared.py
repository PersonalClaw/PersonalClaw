"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_docker_single_container.py``

Its checks:
``tests/rails/test_docker_single_container_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
