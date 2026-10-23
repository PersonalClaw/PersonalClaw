"""The source-tree checks split from
``tests/test_npm_advisory_floors.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from ci_catalogue_steps import read, steps_naming
from npm_advisory_floors_shared import _REQUIRE_TREE


def test_ci_runs_this_file_where_the_tree_is_installed_and_requires_it():
    """The copy check skips without node_modules, and a skip reads as a pass, so the CI job that
    installs the tree for it must also say the tree has to be there. Without this rail, dropping
    that step, its `npm ci` or its lever would leave the check skipped on every run. Read from
    the catalogue, which both of CI's shapes run (``tests/ci_catalogue_steps.py``): the job
    installs the tree (`npm-ci` in its toolchain) and the step sets the lever."""
    runs = [
        (job.id, "npm-ci" in job.toolchain, {**job.env, **step.env}.get(_REQUIRE_TREE) == "1")
        for job, step in steps_naming("tests/test_npm_advisory_floors.py", read())
    ]
    assert runs, "no catalogue step runs tests/test_npm_advisory_floors.py on an installed tree"
    assert all(installed and required for _, installed, required in runs), (
        "each catalogue job that runs this file needs `npm-ci` in its toolchain and "
        f'`{_REQUIRE_TREE} = "1"` in its step env; found (job, installed, required): {runs}'
    )
