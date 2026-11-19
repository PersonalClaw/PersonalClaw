"""The merge controller's tests plant each gate as main holds it.

``tests/test_ci_merge_controller.py`` plants each gate of the merge verdict (rule 5b) in main's
tree with its ``pull_request`` filter, its ``merge_group`` trigger and its jobs' bounds, from
``tests/ci_merge_controller_shared.py``, so its rows judge the gates main has. This rail holds the
two equal. It sits outside the CI contract's rails on purpose: main's contract lays its own copy of
the shared module over every head, so a head that changes a gate's paths or a bound would be red
there until a second change caught the fixture up.

It also holds the verdict's list of what writes CI's ``infrastructure`` error (#5848): a job red
only on it is flake-class, so every writer of it in CI's code (``.github``, ``tools``, ``scripts``)
is one ``merge_controller.INFRA_WRITERS`` names, and a head that changes one owns its error.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from ci_merge_controller_shared import GATE_BOUNDS, GATE_FILTERS
from rails_repo import REPO

from tools.ci import merge_controller as mc
from tools.ci import workflows


def _doc(gate: workflows.Gate) -> Any:
    return yaml.safe_load((REPO / ".github" / "workflows" / gate.file).read_text(encoding="utf-8"))


@pytest.mark.parametrize("gate", workflows.GATES, ids=lambda g: g.file)
def test_the_merge_controller_tests_plant_each_gate_as_main_holds_it(gate: workflows.Gate) -> None:
    doc = _doc(gate)
    assert workflows.pull_request_paths(doc) == GATE_FILTERS[gate.file]
    # Every gate runs on every queue commit, which the queue commit's verdict reads.
    assert workflows.triggers(doc)["merge_group"] == {"types": ["checks_requested"]}
    for job in gate.jobs:
        assert doc["jobs"][job]["timeout-minutes"] == GATE_BOUNDS[gate.file], job


def test_the_shared_fixture_names_every_gate_and_no_other() -> None:
    files = {gate.file for gate in workflows.GATES}
    assert set(GATE_FILTERS) == files
    assert set(GATE_BOUNDS) == files


#: A line that writes the error, by its title or by the audit's name for it.
_WRITES_INFRA = re.compile(r"::error title=(?:infrastructure|\{INFRA_TITLE\})::")
_CI_CODE = (".github", "tools", "scripts")


def _infra_writers(root: Path) -> list[str]:
    """Every file of CI's code under *root* that writes the infrastructure error, sorted."""
    found: list[str] = []
    for top in _CI_CODE:
        for dirpath, dirs, files in os.walk(root / top):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", "node_modules"}]
            for name in files:
                path = Path(dirpath, name)
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if _WRITES_INFRA.search(text):
                    found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _unnamed(writers: list[str]) -> list[str]:
    return [w for w in writers if not w.startswith(mc.INFRA_WRITERS)]


def test_every_writer_of_the_infrastructure_error_is_one_the_verdict_names() -> None:
    writers = _infra_writers(REPO)
    assert ".github/actions/setup/action.yml" in writers, f"the scan found {writers}"
    assert not _unnamed(
        writers
    ), f"merge_controller.INFRA_WRITERS names none of {_unnamed(writers)}"
    for entry in mc.INFRA_WRITERS:
        assert any(w.startswith(entry) for w in writers), f"{entry} writes no such error"


def test_a_planted_writer_the_verdict_does_not_name_is_found(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "fetch.sh").write_text('echo "::error title=infrastructure::down"\n')
    (tmp_path / "tools" / "ci").mkdir(parents=True)
    (tmp_path / "tools" / "ci" / "audit.py").write_text(
        'print(f"::error title={INFRA_TITLE}::x")\n'
    )
    assert _infra_writers(tmp_path) == ["scripts/fetch.sh", "tools/ci/audit.py"]
    assert _unnamed(_infra_writers(tmp_path)) == ["scripts/fetch.sh"]
