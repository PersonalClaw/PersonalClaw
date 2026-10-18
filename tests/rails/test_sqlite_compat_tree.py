"""The source-tree checks split from
``tests/test_sqlite_compat.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO


def test_no_production_site_uses_a_bare_with_on_a_connection():
    """``with sqlite3.connect(...)`` does NOT close the connection.

    Its context manager ends the TRANSACTION and leaves the handle open, so the shape is a
    per-call resource leak that only shows up as a `ResourceWarning: unclosed database`
    charged to whichever test happened to trigger the collecting GC. Five such sites (two
    in `snapshot.py`, three in `durability/shards.py`) were what kept the suite from
    reaching zero after the teardown fixture landed, and in a long-lived gateway the hourly
    incremental export leaked one handle per table per entry. The correct shape, already
    used elsewhere in `snapshot.py`, is ``with closing(sqlite3.connect(...)) as conn:``.

    Population is ZERO, so this ratchet has nothing to grandfather.
    """
    import re

    src = REPO / "src" / "personalclaw"
    files = list(source_index.package_files())
    assert len(files) > 100, f"only {len(files)} source files scanned — did the tree move?"

    pattern = re.compile(r"with\s+(?:\w+\.)*sqlite3\.connect\(")
    offenders = [
        f"{p.relative_to(src)}:{i}"
        for p in files
        for i, line in enumerate(source_index.text(p).splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        "a bare `with sqlite3.connect(...)` leaves the connection open — wrap it in "
        f"contextlib.closing(): {offenders}"
    )
