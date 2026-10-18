"""The source-tree checks split from
``tests/test_tools_handler.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO


def test_only_two_execution_paths_exist_and_both_are_gated():
    """The census. This fix is complete only if no THIRD path calls a provider ungated.

    `runtime.py` dispatches through an index built with the disabled filter already
    applied at assembly, and `handlers/tools.py` now checks before dispatch. A new
    `.invoke(` on a tool provider needs its own gate, and this reds until it has one.
    """
    import re

    src = REPO / "src" / "personalclaw"
    # File → how many dispatch sites it holds. Files and counts, deliberately NOT line
    # numbers: an edit anywhere above a call site would move its line and red this test
    # for a reason that has nothing to do with gating. A SECOND `.invoke(` appearing in an
    # already-listed file is still caught, because the count changes.
    hits: dict[str, int] = {}
    for py in source_index.package_files():
        for line in source_index.text(py).splitlines():
            if re.search(r"\b(prov|provider)\.invoke\(", line):
                rel = py.relative_to(src).as_posix()
                hits[rel] = hits.get(rel, 0) + 1
    assert hits == {
        "agents/native/runtime.py": 1,
        "dashboard/handlers/tools.py": 1,
    }, (
        "the set of tool-execution call sites changed — a new one needs the same "
        f"tool_prefs gate before it dispatches. Found: {hits}"
    )
