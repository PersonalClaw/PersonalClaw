"""The source-tree checks split from
``tests/test_one_embedding_control_plane.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from one_embedding_control_plane_shared import REPO, RETIRED_PATHS


def test_no_frontend_reference_to_the_retired_plane_remains():
    """The census that justified the deletion, kept executable so it cannot rot."""
    web = REPO / "web/src"
    hits = [
        f"{path.relative_to(REPO)}:{token}"
        for path in source_index.web_files()
        if path.suffix.startswith(".ts") and path.is_relative_to(web)
        for token in RETIRED_PATHS
        if token in source_index.text(path)
    ]
    assert hits == [], f"a frontend caller appeared: {hits}"
