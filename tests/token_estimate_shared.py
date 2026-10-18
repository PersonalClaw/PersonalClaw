"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_token_estimate.py``

Its checks:
``tests/rails/test_token_estimate_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


_SRC = _REPO_ROOT / "src" / "personalclaw"


#: The canonical home. Every other module must import from it, not restate it.
_CANONICAL = _SRC / "token_estimate.py"


#: A local definition of the ratio, under any of the names the ten sites had used. Matches
#: ``CHARS_PER_TOKEN = 4``, ``_CHARS_PER_TOKEN = 3.0`` and ``CHARS_PER_TOKEN: int = 4``; a
#: RHS that names another constant (``= NOMINAL_CHARS_PER_TOKEN``) is a re-export, not a
#: redefinition, so it deliberately does not match.
_LOCAL_DEFINITION = re.compile(r"_?CHARS_PER_TOKEN(?::\s*[a-z]+)?\s*=\s*[0-9]", re.IGNORECASE)
