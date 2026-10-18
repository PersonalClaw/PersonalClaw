"""The source-tree checks split from
``tests/test_session_share.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from session_share_shared import _share_call_sites

import personalclaw


def _core_sources() -> dict[str, str | ast.Module]:
    """Every package module's tree, from the suite's one source index."""
    root = Path(personalclaw.__file__).resolve().parent
    return {str(p.relative_to(root)): tree for p, tree in source_index.package_trees()}


def test_share_has_exactly_one_call_site():
    """The "never auto-published" proof. A behavioural test only covers the paths it
    drives; this covers the ones it doesn't — a heartbeat tick, a post-turn hook or a
    "share it while we're here" convenience reds HERE rather than quietly publishing
    conversations in production.
    """
    assert _share_call_sites(_core_sources()) == {
        ("dashboard/session_starters.py", "api_session_share")
    }
