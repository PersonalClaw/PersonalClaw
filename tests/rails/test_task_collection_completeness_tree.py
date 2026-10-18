"""The source-tree checks split from
``tests/test_task_collection_completeness.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
import task_collection_completeness_shared
from rails_repo import REPO

_own_home = task_collection_completeness_shared._own_home


def test_no_caller_still_asks_for_everything_with_a_magic_limit():
    """The census that motivated the primitive, kept executable. Fourteen call sites meant "all
    of them" and said `limit=10_000` / `limit=500`; each was a silent truncation waiting for a
    busy install (a loop's progress count, a project delete, a reset gate). A window is fine
    where a window is intended — those callers name a small explicit one."""
    import re

    root = REPO / "src"
    offenders = [
        f"{path.relative_to(root)}:{i}"
        for path in source_index.package_files()
        for i, line in enumerate(source_index.text(path).splitlines(), 1)
        if re.search(r"list_all_tasks\([^)]*limit=(?:500|10_000|10000)", line)
    ]
    assert offenders == [], f"these mean collect_tasks(): {offenders}"
