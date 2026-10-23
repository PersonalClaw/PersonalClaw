"""The source-tree checks split from
``tests/test_gate_aggregate_runs_in_ci.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.

The call site is read from the catalogue (``tests/ci_catalogue_steps.py``), whose steps both of
CI's shapes run: ``ci.yml``'s jobs, held to them token for token, and the tier workflows' stage
jobs through ``run-job``. A step there is an argv, so no comment can stand in for the call.
"""

from __future__ import annotations

import dataclasses

from ci_catalogue_steps import read, steps_naming

from tools.ci.catalogue import Catalogue

#: The script the aggregate lives in. `make gates` is a thin wrapper over it, and CI
#: invokes the script directly so a failure names the gate rather than "make: *** Error 1".
_AGGREGATE = "scripts/gate_report.py"


def _runners(cat: Catalogue) -> list[str]:
    """Each change-tier job a step of which runs the aggregate: every pull request runs it."""
    return sorted(job.id for job, _ in steps_naming(_AGGREGATE, cat) if "change" in job.tiers)


def test_the_catalogue_is_read() -> None:
    """Vacuity floor: a reader that read nothing would make the assertion below vacuous."""
    steps = [step for job in read().jobs for step in job.command]
    assert len(steps) > 15, f"read only {len(steps)} catalogue steps; the reader is broken"
    assert any("pytest" in step.argv for step in steps), "no pytest step: steps are not read"


def test_the_gate_aggregate_is_executed() -> None:
    """The load-bearing assertion: a change-tier job RUNS the aggregate."""
    assert _runners(read()), (
        f"no change-tier catalogue step runs {_AGGREGATE}. The six ratchets then reach CI only"
        " through their individual pytest counterparts, and a gate without one does not reach"
        " CI at all."
    )


def test_a_catalogue_without_the_call_fails_this_rail() -> None:
    """The positive control: the real catalogue less the step that calls the aggregate, and
    the same step moved to a merge-tier job, each leave no runner."""
    cat = read()
    without = Catalogue(
        jobs=tuple(
            dataclasses.replace(
                job, command=tuple(s for s in job.command if _AGGREGATE not in s.argv)
            )
            for job in cat.jobs
        )
    )
    assert _runners(cat) and not _runners(without)
    merge_only = Catalogue(
        jobs=tuple(
            dataclasses.replace(job, tiers=("merge",)) if job.id in _runners(cat) else job
            for job in cat.jobs
        )
    )
    assert not _runners(merge_only)
