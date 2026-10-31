"""``python -m tools.ci contract``: main's CI contract judges a head's definitions (#4584 step 4).

Each plant takes this checkout's committed tree as a head, edits it, and runs the real overlay
and the real pytest run on it with the one rail its edit trips, and runs that rail on the tree
before the edit as its control, so it stays a test and not a CI run: each runs in seconds. The
whole contract over an unedited tree is what the contract job runs.

The tree is laid out once for the module's plants, and each starts from it as it was laid out
(:func:`_lay_out_again`): a worktree per plant wrote its 9,239 files every time, which a loaded
disk took long enough to put a plant past its cap.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import timeout_marks
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
#: The row that reads every timeout mark in the tree against the marks list, and its own entry.
_CONTRACT_RAIL = "tests/rails/test_ci_contract.py"
_MARKS_ROW = "every-timeout-mark-is-listed-with-its-reason"
_MARKS_ROW_SITE = f"{_CONTRACT_RAIL}::ROW_TIMEOUTS[{_MARKS_ROW}]"


def _security(head: Path) -> int:
    return contract.run(REPO, head, workers="0", subset=[_SECURITY_RAIL], only=_SECURITY_TEST)


#: What points a git at a repository, which a calling hook can export: never inherited, so each
#: git here works on the folder it is named. The suite's own git settings (no system file, a global
#: file of the test's, no credential helper: ``tests/git_helper_guard.py``) are kept.
_REPOSITORY_VARIABLES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
        "GIT_CONFIG_PARAMETERS",
    }
)


def _git(where: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _REPOSITORY_VARIABLES}
    argv = ["git", "-C", str(where), *args]
    return subprocess.run(argv, capture_output=True, check=True, env=env, text=True).stdout


@dataclass(frozen=True)
class _Laid:
    """The module's head: where it is, the commit it was laid out at, and every ref it saw then
    (a stash included)."""

    where: Path
    sha: str
    refs: str


def _refs(where: Path) -> str:
    return _git(where, "for-each-ref", "--format=%(refname) %(objectname)")


@pytest.fixture(scope="module")
def _laid_head(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Laid]:
    """This checkout's committed tree, laid out once as a head the way the contract job lays one
    out: a detached worktree at the commit, since the rails read what git tracks. It is a
    worktree of a clone of this checkout's repository that borrows its objects, so what a plant
    commits, and any ref or stash it makes, never reaches this checkout or its other worktrees."""
    root = tmp_path_factory.mktemp("laid-head")
    sha = _git(REPO, "rev-parse", "HEAD").strip()
    _git(root, "clone", "--quiet", "--shared", "--no-checkout", str(REPO), "repo")
    _git(root / "repo", "worktree", "add", "--quiet", "--detach", str(root / "head"), sha)
    try:
        yield _Laid(root / "head", sha, _refs(root / "head"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _assert_as_laid(laid: _Laid) -> None:
    """The head is at the commit it was laid out at, holds no ref or stash it did not hold then,
    and holds what git tracks there and nothing else."""
    at = _git(laid.where, "rev-parse", "HEAD").strip()
    assert at == laid.sha, f"the head is at {at}, not the {laid.sha} it was laid out at"
    refs = _refs(laid.where)
    assert refs == laid.refs, f"a plant left refs or a stash: {refs!r}, not {laid.refs!r}"
    status = _git(laid.where, "status", "--porcelain", "--ignored", "--untracked-files=all")
    assert status == "", f"the head holds what its commit does not:\n{status}"


def _lay_out_again(laid: _Laid) -> None:
    """The head as it was laid out, whatever the plant before did in it: back at the recorded
    commit (never its own HEAD, which a plant that commits moves), with every file git tracks
    there as it is there, and every other file gone, ignored ones and bytecode included."""
    _git(laid.where, "reset", "--quiet", "--hard", laid.sha)
    _git(laid.where, "clean", "-ffdxq")
    _assert_as_laid(laid)


@pytest.fixture
def head(_laid_head: _Laid, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The module's head as it was laid out (:func:`_lay_out_again`), and a bytecode folder of the
    plant's own for the children it runs. The suite keeps bytecode in one folder per run, by each
    source's path (``tests/pycache_guard.py``), and every plant's head is at the same path: a
    same-size edit a plant's child compiled within the second would otherwise run in the next."""
    _lay_out_again(_laid_head)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "bytecode"))
    return _laid_head.where


def _edit(head: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    path = head / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{rel} no longer holds {old!r}: re-point the plant"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


#: Plumbing commits run no hook; an identity of their own, so none is read from the machine.
_PLANTER = ("-c", "user.name=a plant", "-c", "user.email=plant@example.invalid")


def _commit_everything(head: Path) -> str:
    """What the plant changed in *head*, committed and made its HEAD; the commit."""
    _git(head, "add", "--all")
    tree = _git(head, "write-tree").strip()
    parent = _git(head, "rev-parse", "HEAD").strip()
    commit = _git(head, *_PLANTER, "commit-tree", tree, "-p", parent, "-m", "a plant").strip()
    _git(head, "update-ref", "--no-deref", "HEAD", commit)
    return commit


def _changes_one_of_each(head: Path) -> None:
    _edit(head, "tools/ci/catalogue.toml", '  "security",\n', "")
    (head / "AGENTS.md").unlink()
    (head / "tools/ci/__init__.py").chmod(0o755)
    (head / "planted").mkdir()
    (head / "planted/added.txt").write_text("added\n", encoding="utf-8")
    (head / "tools/ci/__pycache__").mkdir(exist_ok=True)
    (head / "tools/ci/__pycache__/planted.cpython-313.pyc").write_bytes(b"ignored\n")


def test_a_plant_that_commits_in_the_head_leaves_the_next_at_the_laid_out_commit(
    _laid_head: _Laid, head: Path, tmp_path: Path
) -> None:
    """The plants share one head (:func:`_laid_head`): one that edits, removes, adds and ignores
    files in it and commits them, moving its HEAD, leaves the next at the commit the head was
    laid out at, holding that commit's files and nothing else."""
    _changes_one_of_each(head)
    assert _commit_everything(head) != _laid_head.sha
    _lay_out_again(_laid_head)
    for rel in ("tools/ci/catalogue.toml", "AGENTS.md", "tools/ci/__init__.py"):
        assert (head / rel).read_text(encoding="utf-8") == _git(
            head, "show", f"{_laid_head.sha}:{rel}"
        ), rel
    assert not (head / "tools/ci/__init__.py").stat().st_mode & 0o111
    assert not (head / "planted").exists()
    assert not (head / "tools/ci/__pycache__/planted.cpython-313.pyc").exists()
    bytecode = Path(os.environ["PYTHONPYCACHEPREFIX"])
    assert bytecode.parent == tmp_path and not bytecode.exists(), "a plant's children share none"


def test_a_head_off_its_commit_or_with_a_ref_or_stash_of_its_own_is_never_planted_in(
    _laid_head: _Laid, head: Path
) -> None:
    """The check each plant's head passes first bites: a head put back to its own HEAD after a
    commit, one holding a branch a plant made, and one holding a stash, are each refused."""
    _changes_one_of_each(head)
    _commit_everything(head)
    _git(head, "reset", "--quiet", "--hard", "HEAD")  # the plant's commit, not the laid-out one
    with pytest.raises(AssertionError, match="it was laid out at"):
        _assert_as_laid(_laid_head)
    _lay_out_again(_laid_head)
    _git(head, "branch", "a-plants-branch")
    with pytest.raises(AssertionError, match="left refs or a stash"):
        _assert_as_laid(_laid_head)
    _git(head, "branch", "--delete", "--force", "a-plants-branch")
    (head / "AGENTS.md").write_text("stashed\n", encoding="utf-8")
    _git(head, *_PLANTER, "stash", "push", "--quiet")
    with pytest.raises(AssertionError, match="left refs or a stash"):
        _assert_as_laid(_laid_head)
    _git(head, "stash", "drop", "--quiet")
    _lay_out_again(_laid_head)


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
        if rel != contract.MARKS:
            assert (head / rel).read_bytes() == (REPO / rel).read_bytes(), rel
    # The marks list holds main's entries for the files the overlay wrote, and the head's own (it
    # has none) for the rest.
    ours, _ = timeout_marks.listed(REPO)
    overlaid = {s: v for s, v in ours.items() if s.partition("::")[0] in written}
    assert _MARKS_ROW_SITE in overlaid
    assert timeout_marks.listed(head) == (overlaid, [])
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
    _edit(head, "tools/ci/plan.py", '"docker")', '"docker", "jq")')
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


def _marks_row(head: Path) -> int:
    return contract.run(REPO, head, workers="0", subset=[_CONTRACT_RAIL], only=_MARKS_ROW)


def _listed(head: Path) -> dict[str, timeout_marks.Cap]:
    return {site: cap for site, (cap, _) in timeout_marks.listed(head)[0].items()}


@_TREE
def test_a_head_that_lists_a_marked_test_of_its_own_passes(head: Path) -> None:
    """The head's own tests are its own to list: main's list names none of them."""
    site = "tests/test_a_planted_slow_test.py::test_it_is_slow"
    (head / "tests/test_a_planted_slow_test.py").write_text(
        "import pytest\n\n\n@pytest.mark.timeout(45)\ndef test_it_is_slow():\n    pass\n", "utf-8"
    )
    # The control: unlisted, the row reads the head's mark and is red.
    assert _marks_row(head) != 0
    with (head / contract.MARKS).open("a", encoding="utf-8") as marks:
        marks.write(f'\n[[mark]]\nsite = "{site}"\ncap = 45\nreason = "the head\'s own"\n')
    assert _marks_row(head) == 0
    assert _listed(head)[site] == 45


@_TREE
def test_a_head_that_raises_a_contract_rails_mark_is_judged_at_mains_cap(head: Path) -> None:
    """The rail is main's again, and so is its entry: the head's raise is overlaid in both."""
    ours = _listed(REPO)[_MARKS_ROW_SITE]
    raised = ours * 10
    _edit(
        head,
        _CONTRACT_RAIL,
        f'    "{_MARKS_ROW}": pytest.mark.timeout({ours}),\n',
        f'    "{_MARKS_ROW}": pytest.mark.timeout({raised}),\n',
    )
    _edit(
        head,
        contract.MARKS,
        f'site = "{_MARKS_ROW_SITE}"\ncap = {ours}\n',
        f'site = "{_MARKS_ROW_SITE}"\ncap = {raised}\n',
    )
    assert _listed(head)[_MARKS_ROW_SITE] == raised
    # Read by the head's list, main's rail would be red at its own cap; the overlay's list is green.
    assert _marks_row(head) == 0
    assert _listed(head)[_MARKS_ROW_SITE] == ours
    assert (head / _CONTRACT_RAIL).read_bytes() == (REPO / _CONTRACT_RAIL).read_bytes()


_ENTRY = '[[mark]]\nsite = "{site}"\ncap = {cap}\nreason = "{why}"\n'


def _marks(*entries: tuple[str, int, str]) -> str:
    head = "# the list's own header\n\n"
    return head + "\n".join(_ENTRY.format(site=s, cap=c, why=w) for s, c, w in entries)


def test_the_marks_list_is_mains_for_what_the_overlay_writes_and_the_heads_for_the_rest() -> None:
    main_list = _marks(
        ("tests/rails/test_ci_a.py::test_x", 60, "main's rail"),
        ("tests/test_b.py::test_y", 40, "main's copy of a test the head holds its own copy of"),
    )
    head_list = _marks(
        ("tests/rails/test_ci_a.py::test_x", 600, "the head's raise of main's rail"),
        ("tests/test_b.py::test_y", 45, "the head's own"),
        ("tests/test_c.py::test_z", 90, "the head's new test"),
    )
    merged = contract.overlaid_marks(main_list, head_list, {"tests/rails/test_ci_a.py"})
    assert merged == _marks(
        ("tests/rails/test_ci_a.py::test_x", 60, "main's rail"),
        ("tests/test_b.py::test_y", 45, "the head's own"),
        ("tests/test_c.py::test_z", 90, "the head's new test"),
    )


@pytest.mark.parametrize(
    "head_list",
    [None, "[[mark]]\nsite = \n", "[[mark]]\ncap = 9\n", "[[mark]]\nsite = 1\ncap = 9\n"],
    ids=["no list", "a list that does not parse", "an entry with no site", "a site not a name"],
)
def test_a_head_list_that_does_not_read_adds_none_of_its_entries(head_list: str | None) -> None:
    main_list = _marks(
        ("tests/rails/test_ci_a.py::test_x", 60, "main's rail"),
        ("tests/test_b.py::test_y", 40, "main's copy"),
    )
    merged = contract.overlaid_marks(main_list, head_list, {"tests/rails/test_ci_a.py"})
    assert merged == _marks(("tests/rails/test_ci_a.py::test_x", 60, "main's rail"))


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
