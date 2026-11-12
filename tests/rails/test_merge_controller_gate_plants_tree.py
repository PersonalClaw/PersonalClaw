"""The merge controller's tests plant each gate as main holds it.

``tests/test_ci_merge_controller.py`` plants each gate of the merge verdict (rule 5b) in main's
tree with its ``pull_request`` filter and its jobs' bounds, from
``tests/ci_merge_controller_shared.py``, so its rows judge the gates main has. This rail holds the
two equal. It sits outside the CI contract's rails on purpose: main's contract lays its own copy of
the shared module over every head, so a head that changes a gate's paths or a bound would be red
there until a second change caught the fixture up.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from ci_merge_controller_shared import GATE_BOUNDS, GATE_FILTERS
from rails_repo import REPO

from tools.ci import workflows


def _doc(gate: workflows.Gate) -> Any:
    return yaml.safe_load((REPO / ".github" / "workflows" / gate.file).read_text(encoding="utf-8"))


@pytest.mark.parametrize("gate", workflows.GATES, ids=lambda g: g.file)
def test_the_merge_controller_tests_plant_each_gate_as_main_holds_it(gate: workflows.Gate) -> None:
    doc = _doc(gate)
    assert workflows.pull_request_paths(doc) == GATE_FILTERS[gate.file]
    for job in gate.jobs:
        assert doc["jobs"][job]["timeout-minutes"] == GATE_BOUNDS[gate.file], job


def test_the_shared_fixture_names_every_gate_and_no_other() -> None:
    files = {gate.file for gate in workflows.GATES}
    assert set(GATE_FILTERS) == files
    assert set(GATE_BOUNDS) == files
