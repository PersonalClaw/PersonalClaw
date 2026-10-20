"""The source-tree checks split from
``tests/test_triggers_loop.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO


def test_nothing_in_the_tree_creates_a_trigger_session(tmp_path):
    """The census behind the fix, so the reasoning cannot rot.

    If a future change DOES start creating trigger sessions, this reds — and at that point the
    direct-execution branch should be revisited rather than left as a second path. That is the
    point of pinning it: the branch exists because this is true, not as belt-and-braces.
    """
    import re

    src = REPO / "src" / "personalclaw"
    creators = []
    for py in source_index.package_files():
        for n, line in enumerate(source_index.text(py).splitlines(), 1):
            if "get_or_create(" not in line or "def get_or_create" in line:
                continue
            if re.search(r"session_key_for|cron:|trigger", line):
                creators.append(f"{py.relative_to(src).as_posix()}:{n}")
    assert creators == [], (
        "something now creates a session for a trigger key — revisit "
        f"`_execute_delivery`'s direct-run branch instead of keeping two paths: {creators}"
    )
