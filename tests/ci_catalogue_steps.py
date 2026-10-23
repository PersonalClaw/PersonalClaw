"""What a candidate's CI runs, read from the catalogue, in both of its shapes (#3915).

Until the flip deletes it, ``ci.yml``'s jobs run their own steps, and ``pr.yml``'s and
``merge.yml``'s stage jobs run the catalogue's through ``run-job``. The contract row "every
catalogue command is the workflow's" (``tests/rails/test_ci_contract.py``) holds each ``ci.yml``
job's validating steps to its entry's, token for token, with the same working directory, step env
and job env. So a rail that reads an entry's steps here holds what both shapes run, and still
holds it once ``ci.yml`` is gone.
"""

from __future__ import annotations

from rails_repo import REPO

from tools.ci import catalogue
from tools.ci.catalogue import Catalogue, Job, Step

#: The tree's catalogue.
CATALOGUE = REPO / catalogue.CATALOGUE


def read() -> Catalogue:
    """The tree's catalogue, read from its file here, so a rail that calls this reads the tree
    as the rails census counts it (``tools/ci/rails_census.py``)."""
    return catalogue.load(CATALOGUE.read_text(encoding="utf-8"), catalogue.CATALOGUE)


def line(job: Job, step: Step) -> str:
    """One step as a line: its job's env and its own, then its argv, as :meth:`Step.render`
    writes it, so a match against a workflow's ``run:`` line reads the same."""
    env = "".join(f"{name}={value} " for name, value in job.env.items())
    return env + step.render()


def text(job: Job) -> str:
    """The job's steps, a line each (:func:`line`)."""
    return "\n".join(line(job, step) for step in job.command)


def steps_naming(word: str, cat: Catalogue) -> list[tuple[Job, Step]]:
    """Each step of *cat* whose argv names *word* as one of its words, with its job."""
    return [(job, step) for job in cat.jobs for step in job.command if word in step.argv]
