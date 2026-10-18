"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_one_embedding_control_plane.py``

Its checks:
``tests/rails/test_one_embedding_control_plane_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

RETIRED_PATHS = (
    "/api/memory/embedding-status",
    "/api/memory/enable-embeddings",
    "/api/memory/disable-embeddings",
    "/api/memory/embedding-models",
    "/api/memory/delete-model",
    "/api/memory/activate-model",
)


REPO = Path(__file__).resolve().parents[1]
