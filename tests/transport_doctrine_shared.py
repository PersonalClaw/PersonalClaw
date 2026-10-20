"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_transport_doctrine.py``

Its checks:
``tests/rails/test_transport_doctrine_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

# Anchor source reads to the package root (this file is <root>/tests/…) so these
# structural guards pass regardless of the pytest invocation cwd — they read repo
# files by relative path and were silently cwd-fragile (only passed when run from
# the PersonalClaw/ dir, failing from the repo root).
_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text()
