"""A child a test starts imports ``personalclaw`` from the tree under test.

pytest imports this checkout's ``personalclaw``, but a child it starts finds one through the venv's
editable install, which can point at another clone. ``tests/conftest.py`` puts this tree's ``src``
first on the run's ``PYTHONPATH`` before any test (``tree_under_test.put_first``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import nested_pytest
import pytest
import tree_under_test

ROOT = Path(__file__).resolve().parents[1]


def test_a_child_started_with_the_runs_environment_imports_this_tree() -> None:
    """A plain child, started as most tests start theirs, imports the ``personalclaw`` this
    process imported."""
    import personalclaw

    done = subprocess.run(
        [sys.executable, "-c", "import personalclaw; print(personalclaw.__file__)"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    theirs = done.stdout.strip()
    assert Path(theirs).resolve() == Path(personalclaw.__file__).resolve(), theirs


@pytest.mark.timeout(50)
def test_a_run_with_another_personalclaw_on_its_path_still_hands_its_children_this_tree(
    tmp_path,
) -> None:
    """🔴 Red before: the test above, run with another ``personalclaw`` ahead of this tree on the
    path a child inherits, as a venv's editable install that points at another clone puts one:
    the child imported that one, while pytest itself imported this tree (pyproject's
    ``pythonpath`` comes first on its own path). On a Mac with several clones a probe's child
    died that way, on a ``handlers_system`` with no ``_IDENTITY``."""
    other = tmp_path / "other-clone" / "personalclaw"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["PYTHONPATH"] = str(other.parent)
    one = f"{Path(__file__)}::test_a_child_started_with_the_runs_environment_imports_this_tree"
    ran = subprocess.run(
        [
            *nested_pytest.argv(tmp_path),
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
        ]
        + ["--no-cov", "--color=no", one],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    said = ran.stdout + ran.stderr
    assert ran.returncode == 0, said
    assert "1 passed" in said, said


def test_the_tree_goes_first_once_and_the_rest_stay_in_their_order() -> None:
    tree = tree_under_test.src()
    environ = {"PYTHONPATH": os.pathsep.join(["/elsewhere", tree, "/after"])}
    tree_under_test.put_first(environ)
    assert environ["PYTHONPATH"] == os.pathsep.join([tree, "/elsewhere", "/after"])
    tree_under_test.put_first(environ)
    assert environ["PYTHONPATH"] == os.pathsep.join([tree, "/elsewhere", "/after"])
    unset: dict[str, str] = {}
    tree_under_test.put_first(unset)
    assert unset == {"PYTHONPATH": tree}
