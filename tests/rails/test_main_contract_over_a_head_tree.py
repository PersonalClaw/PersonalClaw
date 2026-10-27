"""``python -m tools.ci contract``: main's CI contract judges a head's definitions (#4584 step 4).

Each plant lays out this checkout's committed tree as a head, edits it, and runs the real overlay
and the real pytest run on it with the one rail its edit trips, and runs that rail on the tree
before the edit as its control, so it stays a test and not a CI run: each runs in seconds. The
whole contract over an unedited tree is what the contract job runs.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from rails_repo import REPO

from tools.ci import contract

#: Each plant runs pytest over a whole tree laid out from git: listed in tests/timeout-marks.toml.
_TREE = pytest.mark.timeout(150)

#: The rail a head that drops a required job trips, and its one test that reads the tree.
_SECURITY_RAIL = "tests/rails/test_ci_security_runs_in_its_own_job_tree.py"
_SECURITY_TEST = "test_the_tree_holds_the_rule"
#: The rail that runs the setup action's programs step for each word ``plan.SETUP_PROGRAMS``
#: names, and its one test that does, so a word the action's list does not hold reds there.
_STEPS_RAIL = "tests/rails/test_ci_tier_workflow_steps_tree.py"
_PROGRAMS_TEST = "test_each_listed_program_the_path_lacks_comes_from_the_runners_package_manager"


def _security(head: Path) -> int:
    return contract.run(REPO, head, workers="0", subset=[_SECURITY_RAIL], only=_SECURITY_TEST)


def _git(*args: str) -> None:
    # Named explicitly, so a GIT_DIR or index a calling hook exported does not redirect it.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, check=True, env=env)


@pytest.fixture
def head(tmp_path: Path) -> Iterator[Path]:
    """This checkout's committed tree, laid out as a head the way the contract job lays one out:
    a detached worktree, since the rails read what git tracks."""
    where = tmp_path / "head"
    _git("worktree", "add", "--detach", str(where), "HEAD")
    try:
        yield where
    finally:
        _git("worktree", "remove", "--force", str(where))


def _edit(head: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    path = head / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{rel} no longer holds {old!r}: re-point the plant"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def test_the_contract_is_main_list_and_not_vacuous() -> None:
    rails = contract.contract_rails(REPO)
    assert "tests/rails/test_ci_contract.py" in rails
    assert _SECURITY_RAIL in rails and _STEPS_RAIL in rails
    assert all((REPO / r).is_file() for r in rails)
    helpers = contract.helpers(REPO)
    assert "tests/conftest.py" in helpers and "tests/rails/rails_repo.py" in helpers
    assert not any(Path(h).name.startswith("test_") for h in helpers)


def test_the_overlay_puts_main_copies_over_the_heads(tmp_path: Path) -> None:
    head = tmp_path / "head"
    for rel in [*contract.contract_rails(REPO), *contract.helpers(REPO)]:
        (head / rel).parent.mkdir(parents=True, exist_ok=True)
        (head / rel).write_text("# the head's own copy\n", encoding="utf-8")
    (head / "tests/rails/test_ci_heads_own.py").write_text("def test_it():\n    pass\n")
    written = contract.overlay(REPO, head)
    for rel in written:
        assert (head / rel).read_bytes() == (REPO / rel).read_bytes(), rel
    # A rail main does not hold is the head's, and is not run: the run names main's list alone.
    assert "tests/rails/test_ci_heads_own.py" not in written


def test_a_subset_names_only_contract_rails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not contract rails"):
        contract.run(REPO, tmp_path, subset=["tests/rails/test_main_contract_over_a_head_tree.py"])


@_TREE
def test_a_head_that_drops_a_required_job_is_red_with_its_own_contract_edited(head: Path) -> None:
    # The control: the rail is green on the tree before the edit.
    assert _security(head) == 0
    # Out of the merge tier in both places the catalogue names it, so the catalogue still loads.
    _edit(head, "tools/ci/catalogue.toml", '  "security",\n', "")
    catalogue = (head / "tools/ci/catalogue.toml").read_text(encoding="utf-8")
    block = re.search(r'id = "security"\ntiers = \[([^\]]*)\]', catalogue)
    assert block, "the security job's tiers are not where the plant reads them"
    tiers = block.group(1).replace('"merge", ', "").replace(', "merge"', "")
    _edit(head, "tools/ci/catalogue.toml", block.group(0), f'id = "security"\ntiers = [{tiers}]')
    # And its own copy of the rail that would catch it, made to pass.
    (head / _SECURITY_RAIL).write_text(f"def {_SECURITY_TEST}():\n    pass\n", encoding="utf-8")
    assert _security(head) != 0
    assert (head / _SECURITY_RAIL).read_bytes() == (REPO / _SECURITY_RAIL).read_bytes()


@_TREE
def test_a_head_that_only_adds_a_toolchain_word_passes(head: Path) -> None:
    def judged() -> int:
        return contract.run(REPO, head, workers="0", subset=[_STEPS_RAIL], only=_PROGRAMS_TEST)

    _edit(
        head, "tools/ci/catalogue.py", '        "docker",\n', '        "docker",\n        "jq",\n'
    )
    _edit(
        head,
        "tools/ci/jobs.py",
        '    "docker": "docker",\n',
        '    "docker": "docker",\n    "jq": "jq",\n',
    )
    _edit(head, "tools/ci/plan.py", '"dash", "docker")', '"dash", "docker", "jq")')
    # The control: the word everywhere but the setup action's list is red, so the rail reads it.
    assert judged() != 0
    _edit(
        head,
        ".github/actions/setup/action.yml",
        "            docker) ;;\n",
        "            docker) ;;\n            jq) ;;\n",
    )
    assert judged() == 0


@_TREE
def test_a_head_whose_tools_ci_exits_0_on_import_is_red(head: Path) -> None:
    # The rails import the head's tools/ci, so this ends the child before any rail reports. The
    # verdict is main's reading of the report the child never wrote, not the child's exit status.
    # Its control is the first plant's: the same rail is green on the unedited tree.
    init = head / "tools/ci/__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + "import os\nos._exit(0)\n", "utf-8")
    assert _security(head) != 0


def _report(tmp_path: Path, *cases: str, suite: str = 'name="pytest"') -> Path:
    path = tmp_path / "contract.xml"
    body = "".join(cases)
    path.write_text(f"<testsuites><testsuite {suite}>{body}</testsuite></testsuites>", "utf-8")
    return path


_RAIL = "tests/rails/test_ci_x.py"
_CASE = '<testcase classname="tests.rails.test_ci_x" name="test_a"/>'


def test_a_green_report_names_no_problem(tmp_path: Path) -> None:
    assert contract.report_problems(_report(tmp_path, _CASE), [_RAIL]) == []
    skipped = '<testcase classname="tests.rails.test_ci_x.TestY" name="t"><skipped/></testcase>'
    assert contract.report_problems(_report(tmp_path, skipped), [_RAIL]) == []


@pytest.mark.parametrize(
    ("cases", "suite", "expect"),
    [
        ((), 'name="pytest"', "holds no test"),
        (
            ('<testcase classname="tests.rails.test_ci_x" name="t"><failure/></testcase>',),
            "",
            "failure",
        ),
        (
            ('<testcase classname="tests.rails.test_ci_x" name="t"><error/></testcase>',),
            "",
            "error",
        ),
        ((_CASE,), 'name="pytest" errors="1"', "reports 1 errors"),
        (('<testcase classname="tests.rails.test_ci_other" name="t"/>',), "", "ran no test"),
    ],
    ids=["empty", "a failure", "an error", "an error outside a test", "a rail that ran nothing"],
)
def test_a_report_that_is_not_green_names_why(
    tmp_path: Path, cases: tuple[str, ...], suite: str, expect: str
) -> None:
    problems = contract.report_problems(_report(tmp_path, *cases, suite=suite), [_RAIL])
    assert any(expect in p for p in problems), problems


def test_a_missing_or_broken_report_is_red(tmp_path: Path) -> None:
    assert "wrote no report" in contract.report_problems(tmp_path / "none.xml", [_RAIL])[0]
    (tmp_path / "bad.xml").write_text("<testsuites>", "utf-8")
    assert "not junit" in contract.report_problems(tmp_path / "bad.xml", [_RAIL])[0]
