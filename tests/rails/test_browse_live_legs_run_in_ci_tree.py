"""The source-tree checks split from
``tests/test_browse_live_legs_run_in_ci.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import yaml
from browse_live_legs_run_in_ci_shared import _ROOT
from ci_catalogue_steps import read

_MERGE = _ROOT / ".github" / "workflows" / "merge.yml"


def test_the_leg_runs_on_the_pull_request_gate() -> None:
    """merge.yml, not breadth.yml: a proof that only runs after merge is not a merge gate. So
    merge.yml runs on every pull request, and its tier requires the browse-live entry."""
    on = yaml.safe_load(_MERGE.read_text(encoding="utf-8"))[True]
    assert "pull_request" in on, (
        "merge.yml no longer triggers on pull_request, so the browse proof moved off the merge "
        "gate. Re-derive this rail against whatever gates a merge now."
    )
    cat = read()
    assert "browse-live" in cat.merge_jobs, "the merge tier no longer requires browse-live"
    assert "browse-live" in (cat.every_push or ()), "a push to main no longer runs browse-live"
