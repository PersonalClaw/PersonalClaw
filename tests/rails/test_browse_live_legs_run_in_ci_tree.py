"""The source-tree checks split from
``tests/test_browse_live_legs_run_in_ci.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

from browse_live_legs_run_in_ci_shared import _CI, jobs, run_commands

#: Job ids that have been in this workflow long enough that their absence means the parser
#: broke rather than that CI changed. The vacuity floor for :func:`jobs`.
LONG_STANDING_JOBS = frozenset({"lint", "test", "web", "rails", "harness", "client"})


def test_the_job_scan_is_not_vacuous() -> None:
    """If :func:`jobs` returned nothing, every assertion below would pass."""
    parsed = jobs(_CI.read_text(encoding="utf-8"))
    missing = LONG_STANDING_JOBS - set(parsed)
    assert not missing, f"the job scan lost long-standing jobs {sorted(missing)} — parser broke"
    # The pytest step's HOME is derived, not pinned to a job id (the same discipline the module
    # docstring states for the browse leg): since #2720 the `test` job is an aggregation gate and
    # the `uv run pytest …` step moved to `test-shard`, so anchor the run: scan on any job that
    # runs it rather than on `test` specifically.
    all_run = [cmd for block in parsed.values() for cmd in run_commands(block)]
    assert any(
        cmd.startswith("uv run pytest") for cmd in all_run
    ), "the run: scan broke, not ci.yml"


def test_the_leg_runs_on_the_pull_request_gate() -> None:
    """ci.yml, not breadth.yml: a proof that only runs after merge is not a merge gate."""
    header = _CI.read_text(encoding="utf-8").split("jobs:")[0]
    assert re.search(r"^\s*pull_request:", header, re.M), (
        "ci.yml no longer triggers on pull_request, so the browse proof moved off the merge "
        "gate. Re-derive this rail against whatever gates a merge now."
    )
