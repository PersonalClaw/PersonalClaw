"""The source-tree checks split from
``tests/test_every_store_writer_uses_the_shared_writer.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from every_store_writer_uses_the_shared_writer_shared import findings
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


SHARED_WRITER = SRC / "atomic_write.py"


#: The writers the shared writer cannot serve, and why.
EXEMPT: dict[tuple[str, str], str] = {
    ("dashboard/handlers/files.py", "api_file_write"): (
        "the owner's own file, where they keep it: a save keeps the mode it has, so a script "
        "stays executable, where the shared writer would make any file in the home 0600"
    ),
    ("dashboard/handlers/files.py", "api_file_upload"): (
        "a file the owner uploads into a folder they chose, streamed to disk chunk by chunk and "
        "capped as it arrives; the shared writer takes the whole content in memory"
    ),
    ("service/macos.py", "_write_plist_atomic"): (
        "launchd's own file, in ~/Library/LaunchAgents, outside the home: not a store"
    ),
    ("knowledge_providers/pack_parse.py", "run_parse_script"): (
        "the input of one sandboxed script run, in a temp file tempfile creates 0600 for it"
    ),
    ("schedule_script.py", "run_script_sandboxed"): (
        "the input of one sandboxed script run, in a temp file tempfile creates 0600 for it"
    ),
}


def _scan() -> tuple[dict[tuple[str, str], set[str]], int]:
    found: dict[tuple[str, str], set[str]] = {}
    scanned = 0
    for path, tree in source_index.package_trees():
        if path == SHARED_WRITER:
            continue
        scanned += 1
        rel = path.relative_to(SRC).as_posix()
        for fn, shapes in findings(tree).items():
            found[(rel, fn)] = shapes
    return found, scanned


def test_every_writer_goes_through_the_shared_writer_or_says_why_not():
    found, scanned = _scan()
    assert scanned > 500, f"the scan read {scanned} modules — it did not see the package"
    around = {site: shapes for site, shapes in found.items() if site not in EXEMPT}
    assert not around, (
        "these write a file whole without the shared writer (atomic_write.atomic_write, "
        "atomic_write_bytes or atomic_json_write), which makes the write atomic, 0600 under the "
        "home and announced — use it, or list the writer in EXEMPT with why it cannot: "
        + "; ".join(f"{rel}::{fn} {sorted(shapes)}" for (rel, fn), shapes in sorted(around.items()))
    )
    moved_on = sorted(site for site in EXEMPT if site not in found)
    assert not moved_on, f"no longer goes around the shared writer; drop it from EXEMPT: {moved_on}"
