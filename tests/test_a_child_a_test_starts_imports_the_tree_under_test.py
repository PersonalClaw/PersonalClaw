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
import real_home_guard
import tree_under_test

ROOT = Path(__file__).resolve().parents[1]


def _home_of_its_own(environ, home: Path) -> dict[str, str]:
    """*environ* for a child, with a home of its own at *home* and no ``PERSONALCLAW_HOME``: the
    real-home guard refuses a PersonalClaw child that would resolve the real home
    (``tests/real_home_guard.py``), and a run need not have given this one a scratch home."""
    env = {k: v for k, v in environ.items() if k != "PERSONALCLAW_HOME"}
    env["HOME"] = str(home)
    return env


def _a_child_imports_this_tree(home: Path) -> None:
    import personalclaw

    home.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [sys.executable, "-c", "import personalclaw; print(personalclaw.__file__)"],
        env=_home_of_its_own(os.environ, home),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    theirs = done.stdout.strip()
    assert Path(theirs).resolve() == Path(personalclaw.__file__).resolve(), theirs


def test_a_child_started_with_the_runs_environment_imports_this_tree(tmp_path) -> None:
    """A plain child, started as most tests start theirs, with the run's environment and a home
    of its own, imports the ``personalclaw`` this process imported."""
    _a_child_imports_this_tree(tmp_path / "home")


def test_a_run_under_the_real_home_still_gives_its_child_a_home_of_its_own(
    tmp_path, monkeypatch
) -> None:
    """🔴 Red before (the child took the run's environment as it was): breadth's suite legs, its
    random-order leg and a plain local run keep the runner's own ``HOME`` and set no
    ``PERSONALCLAW_HOME``, and the real-home guard refused the child, "a PersonalClaw child
    process given no isolated home" (run 40633282629, in 5 legs). Under run-job a step's ``HOME``
    is a scratch one, so no Merge leg saw it. Here the run's ``HOME`` is the real home, as the
    guard spells it, with no ``PERSONALCLAW_HOME``, and the child still starts."""
    monkeypatch.setenv("HOME", str(real_home_guard.REAL_HOME.parent))
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    assert not real_home_guard.GUARD.isolates(os.environ), "premise: the run's home is the real one"
    _a_child_imports_this_tree(tmp_path / "home")


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
