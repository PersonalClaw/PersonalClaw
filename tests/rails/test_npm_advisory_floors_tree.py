"""The source-tree checks split from
``tests/test_npm_advisory_floors.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from npm_advisory_floors_shared import _LOCKFILE, _REQUIRE_TREE


def test_ci_runs_this_file_where_the_tree_is_installed_and_requires_it():
    """The copy check skips without node_modules, and a skip reads as a pass, so the CI job that
    installs the tree for it must also say the tree has to be there. Without this rail, dropping
    that step, its `npm ci` or its lever would leave the check skipped on every run."""
    import yaml

    workflow = _LOCKFILE.parent / ".github" / "workflows" / "ci.yml"
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    runs = []
    for job, spec in jobs.items():
        steps = spec.get("steps", [])
        for index, step in enumerate(steps):
            if "tests/test_npm_advisory_floors.py" in str(step.get("run", "")):
                installed = any("npm ci" in str(s.get("run", "")) for s in steps[:index])
                required = (step.get("env") or {}).get(_REQUIRE_TREE) == "1"
                runs.append((job, installed, required))
    assert runs, "no ci.yml step runs tests/test_npm_advisory_floors.py on an installed tree"
    assert all(installed and required for _, installed, required in runs), (
        "each ci.yml step that runs this file needs an `npm ci` before it in its job and "
        f'`{_REQUIRE_TREE}: "1"` in its env; found (job, installed, required): {runs}'
    )
