"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_loop_action_guard_mirror.py``

Its checks:
``tests/rails/test_loop_action_guard_mirror_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


_WEB = _REPO / "web" / "src"


_LINE_COMMENT = re.compile(r"//[^\n]*")


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Blank every comment, preserving newlines so line numbers survive.

    The mirrors in ``lib/loopStatus.ts`` carry long docstrings that name individual statuses, and
    the census patterns here are short enough that prose could satisfy one. Line structure is kept
    so a future failure message can still cite a line.
    """
    text = _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return _LINE_COMMENT.sub("", text)
