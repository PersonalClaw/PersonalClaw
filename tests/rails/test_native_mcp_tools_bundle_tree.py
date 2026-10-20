"""The source-tree checks split from
``tests/test_native_mcp_tools_bundle.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO


def test_the_bundle_is_the_only_copy():
    """No second MCP adapter in core: the bundle is the one implementation of this surface."""
    src = REPO / "src" / "personalclaw"
    offenders = [
        str(p.relative_to(src))
        for p in source_index.package_files()
        if "apps/native/" not in p.as_posix() and "def create_mcp_provider" in source_index.text(p)
    ]
    assert offenders == [], offenders
