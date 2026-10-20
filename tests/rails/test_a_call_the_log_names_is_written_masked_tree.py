"""The source-tree checks split from
``tests/test_a_call_the_log_names_is_written_masked.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from a_call_the_log_names_is_written_masked_shared import _offenders
from rails_repo import REPO

_SRC = REPO / "src" / "personalclaw"


def test_no_log_line_writes_a_calls_title_or_input_raw():
    files = list(source_index.package_files())
    assert len(files) > 300, "the census must read the whole package"
    found = [
        hit
        for path in files
        for hit in _offenders(source_index.tree(path), str(path.relative_to(_SRC)))
    ]
    assert not found, (
        "a log line writes a tool call's title or arguments as it came; write it through "
        "`audit_subject.log_title` (the title) or `audit_text` (anything longer):\n  "
        + "\n  ".join(found)
    )
