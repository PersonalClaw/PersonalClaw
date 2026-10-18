"""The source-tree checks split from
``tests/test_inprocess_tool_providers.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO


def test_get_mcp_registry_has_no_importers():
    """The dead symbol must stay dead: no source module may import it (it does not
    exist, so any importer silently degrades to zero tools)."""

    root = REPO / "src" / "personalclaw"
    offenders = []
    for p in source_index.package_files():
        text = source_index.text(p)
        if "import get_mcp_registry" in text or "mcp_discovery import get_mcp_registry" in text:
            offenders.append(str(p.relative_to(root)))
    assert offenders == [], f"dead get_mcp_registry import resurfaced in: {offenders}"
