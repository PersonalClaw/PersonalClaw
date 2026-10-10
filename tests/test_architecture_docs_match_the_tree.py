"""Architecture docs state facts about the tree; these legs hold them to it.

A reader follows a doc's claim into the code. When the claim drifts (a count the code no
longer has, a module the tree no longer holds), the doc misleads exactly the reader who
trusted it, and nothing else notices. Each leg compares one kind of claim with the tree.
"""

from __future__ import annotations

import re
from pathlib import Path

from personalclaw.security import BUILTIN_DENIED_COMMAND_PATTERNS

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs" / "architecture"

#: A stated size of the built-in command denylist ("116 shell patterns").
_PATTERN_COUNT = re.compile(r"(\d+)\s+shell patterns")


def test_the_security_doc_states_the_denylists_real_size():
    """The security doc's denylist count is the count the gateway enforces.

    It said 112 while the packaged baseline held 116.
    """
    stated = _PATTERN_COUNT.findall((DOCS_DIR / "security.md").read_text(encoding="utf-8"))
    assert stated, "security.md no longer states the denylist's size: rework or drop this leg"
    expected = len(BUILTIN_DENIED_COMMAND_PATTERNS)
    assert [int(n) for n in stated] == [expected] * len(stated), (
        f"security.md says {stated} shell patterns; "
        f"BUILTIN_DENIED_COMMAND_PATTERNS holds {expected}"
    )
