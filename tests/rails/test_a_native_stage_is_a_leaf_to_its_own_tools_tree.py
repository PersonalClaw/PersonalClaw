"""The source-tree checks split from
``tests/test_a_native_stage_is_a_leaf_to_its_own_tools.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

_SRC = REPO / "src" / "personalclaw"


def _reads_a_leaf_key_from_the_environment(line: str) -> bool:
    return "environ" in line and any(
        key in line for key in ("__wf_", "WF_DEPTH_KEY", "LEAF_READ_ONLY_KEY")
    )


def test_every_reader_of_the_leaf_keys_goes_through_the_one_accessor():
    """🔴 Red on main (four readers of the process environment). A reader of its own is how the
    native runtime lost the lineage: it read a place a native session never writes."""
    assert _reads_a_leaf_key_from_the_environment('os.environ.get("__wf_run_id", "")'), "control"
    files = list(source_index.package_files())
    assert len(files) > 500, "vacuity floor: the package was not read"
    offenders = [
        f"{path.relative_to(_SRC)}:{number}"
        for path in files
        for number, line in enumerate(source_index.text(path).splitlines(), 1)
        if _reads_a_leaf_key_from_the_environment(line)
    ]
    assert offenders == [], offenders
