"""Shared cleanup utilities for LLM provider session files.

Provides path safety validation used by all providers before deleting
session files on disk.
"""

import os
from pathlib import Path


def _is_safe_path(target: Path, expected_root: Path) -> bool:
    """Validate target is strictly under expected_root (no traversal).

    Returns True only if the resolved target path is a proper child of
    the resolved expected_root (never equal to it).  Deleting the root
    directory itself is never correct during session cleanup.

    Returns False on any resolution error (broken symlinks, permission
    issues, etc.).
    """
    try:
        resolved = target.resolve()
        root = expected_root.resolve()
        return str(resolved).startswith(str(root) + os.sep)
    except (OSError, ValueError):
        return False
