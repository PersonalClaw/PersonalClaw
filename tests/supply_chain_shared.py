"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_supply_chain.py``

Its checks:
``tests/rails/test_supply_chain_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


README = REPO_ROOT / "README.md"


#: Shell invocations that re-derive `PATH` from `/etc/profile`: a short-flag CLUSTER
#: containing `l` (`-lc`, `-cl`, `-l -c`) or an explicit `--login`. The optional path and
#: shell-name prefixes cover `/bin/sh`, `bash`, `dash`, `ash`, `zsh`, `ksh`.
_LOGIN_SHELL_RE = re.compile(
    r"\b(?:[\w./-]*/)?(?:ba|da|a|z|k)?sh\s+(?:-[A-Za-z]*l[A-Za-z]*|--login)\b"
)


def _logical_lines(script: str) -> list[str]:
    """Shell comments stripped, backslash continuations joined.

    Both halves are load-bearing. **Comments must go first**: a rail that scans raw source
    counts the prose explaining the fix as the defect, and the commit that fixes this one
    necessarily documents `sh -lc` in order to say why it is wrong. Only whole-line comments
    are removed — a `#` mid-command can live inside a quoted string, and guessing wrong there
    would silently drop real code. **Continuations must be joined second**: the defect spanned
    two physical lines (`docker run ... \\` then `sh -lc "$SMOKE"`), so a per-line scan sees a
    container invocation with no shell and a shell with no container, and finds nothing.
    """
    uncommented = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    joined: list[str] = []
    pending = ""
    for line in uncommented:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        joined.append(pending + stripped)
        pending = ""
    if pending:
        joined.append(pending)
    return joined
