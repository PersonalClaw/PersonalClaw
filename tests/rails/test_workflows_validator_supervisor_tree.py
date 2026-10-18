"""The source-tree checks split from
``tests/test_workflows_validator_supervisor.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from workflows_validator_supervisor_shared import _count_policy_callers

from personalclaw.workflows import supervisor_policy as sp


def _census() -> tuple[dict[str, int], int]:
    """Scan every ``src/personalclaw`` module except the declaration itself."""
    module_path = Path(sp.__file__).resolve()
    root = module_path.parents[1]  # .../src/personalclaw
    hits: dict[str, int] = {}
    scanned = 0
    for py, tree in source_index.package_trees():
        if py.resolve() == module_path:
            continue
        scanned += 1
        n = _count_policy_callers(tree)
        if n:
            hits[str(py.relative_to(root))] = n
    return hits, scanned


def test_the_census_scans_real_source_not_nothing():
    """Vacuity control on the SCAN: a rule that matched an empty file set would pass forever."""
    _hits, scanned = _census()
    assert scanned >= 50, f"census only scanned {scanned} modules — it is not seeing src/"


def test_DIRECTION_1_the_wired_policy_still_has_its_production_caller():
    """RED if the last production caller of ``SupervisorPolicy`` disappears while the marker
    claims the policy is wired — the module would have gone inert again without saying so.

    Wiring the policy INVERTED this direction. It used to assert that NO caller existed while
    the marker claimed zero; the marker now claims one, so the drift worth catching is the
    opposite one.
    A deletion that strands `loop_convergence._supervisor_policy` would otherwise leave a
    `SupervisorPolicy`
    nothing reads, declaring itself wired — the exact lie the convention exists to prevent."""
    hits, _scanned = _census()
    assert hits, (
        "SupervisorPolicy claims a production caller but the census found none — either the "
        "wiring was removed (restore it, or set HAS_ZERO_PRODUCTION_CALLERS back to True)"
    )
    assert "loop_convergence.py" in " ".join(
        hits
    ), f"the wired caller is the run controller's convergence step; census found {hits}"


def test_the_marker_and_reality_agree():
    """The coupling invariant both probes derive from: the claim must equal the fact. Unchanged
    by the wiring — the two DIRECTION rails flipped which side of it they guard, but the invariant
    itself is what makes either lie impossible, so it is stated once and never edited."""
    hits, _scanned = _census()
    assert sp.HAS_ZERO_PRODUCTION_CALLERS == (len(hits) == 0)
