"""The source-tree checks split from
``tests/test_stored_times_carry_their_offset.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
import stored_times_carry_their_offset_shared
from rails_repo import REPO
from stored_times_carry_their_offset_shared import zone_less_writers

_isolated_home = stored_times_carry_their_offset_shared._isolated_home


SRC = REPO / "src" / "personalclaw"


def test_no_writer_turns_a_zone_less_clock_reading_into_a_stored_or_served_time():
    found = {
        path.relative_to(SRC).as_posix(): lines
        for path, tree in source_index.package_trees()
        if (lines := zone_less_writers(tree))
    }
    assert len(source_index.package_files()) > 500, "the census read the tree"
    assert found == {}
