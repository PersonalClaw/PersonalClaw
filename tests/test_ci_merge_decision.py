"""``tools/ci/merge_decision.py``: the merge controller's decision, green, red or pending.

The decision counts one run's per-job results against the planned job set:

- the candidate's own run, when it changed no definition (``trust.compare`` on its head);
- otherwise a run of the default branch's definitions on the candidate's tree, the candidate's
  own run being advisory, so a definition it edited cannot judge it.

A planned job that is missing, cancelled, skipped or red is red, even when the candidate's own
workflow dropped it, and so is the job that runs main's catalogue check, which no plan names. A
counted run for an older head or an older partner head is pending, so a changed partner head
takes a green back. The planned set is read from the default branch's catalogue, at main's tip,
never from the candidate's files or the worktree.

Each table asserts its row count, so a dropped row fails the table. The definition-change rows
compare real git objects in a planted repository in ``tmp_path``. Only the import scan reads this
repository's tree, and only this module's own source.

Security-marked: this is a fail-closed boundary (rule 2 in ``docs/maintainers/testing.md``).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from table_rows import row

from tools.ci import catalogue
from tools.ci import merge_decision as md
from tools.ci import merge_verdict as mv
from tools.ci import trust, verdict

pytestmark = pytest.mark.security

HEAD = "a" * 40
TREE = "b" * 40
_STAMP = "Planted <planted@example.invalid> 1700000000 +0000"
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _commit_id(message: str, parent: str | None = None) -> str:
    """The id of a commit of the empty tree with ``_STAMP`` as its author and committer."""
    lines = [f"tree {_EMPTY_TREE}"] + ([f"parent {parent}"] if parent else [])
    body = "\n".join([*lines, f"author {_STAMP}", f"committer {_STAMP}", "", message, ""])
    data = body.encode()
    return hashlib.sha1(b"commit %d\x00" % len(data) + data).hexdigest()


#: main's two commits in the planted repository the ``main_repo`` fixture builds: ``OLDER``, and
#: its child ``BASE``, main's tip. Each id is fixed by its content, so the row tables can name it
#: before the repository exists; the fixture checks that they agree.
OLDER = _commit_id("older")
BASE = _commit_id("base", OLDER)
PARTNER = "d" * 40
NEW_PARTNER = "e" * 40
OLD_HEAD = "f" * 40
#: The older head's own tree: a real older head has one of its own.
OLD_TREE = "c" * 40
OTHER_TREE = "1" * 40
CONTROLLER = "2" * 40
DIGEST = "sha256:" + "3" * 64
TRUSTED_DIGEST = "sha256:" + "4" * 64

PLAN = ("rails", "python-suite", "web-unit")
TOOLCHAIN = {"python": "3.12.15"}
#: A candidate's own run's toolchain, where it must differ from the default branch's run's.
OWN_TOOLCHAIN = {"python": "3.13.9"}


#: The ``ci.yml`` job whose ``catalogue-check`` step runs main's ``tools/ci`` over the candidate's
#: catalogue. No plan names it; the decision requires it of the run it counts.
CHECK_JOB = "changes"


def _needs(*, catalogue_check: str | None = "success", **results: str) -> str:
    """``toJSON(needs)`` of ``results``, with the catalogue check's job as ``catalogue_check``
    says (left out when it is None)."""
    jobs = {key.replace("_", "-"): {"result": r, "outputs": {}} for key, r in results.items()}
    if catalogue_check is not None:
        jobs[CHECK_JOB] = {"result": catalogue_check, "outputs": {}}
    return json.dumps(jobs)


ALL_GREEN = _needs(rails="success", python_suite="success", web_unit="success")


def _run(
    needs: str = ALL_GREEN,
    *,
    definitions: str = HEAD,
    head_sha: str = HEAD,
    tree_oid: str = TREE,
    partners: dict[str, str] | None = None,
    digest: str = DIGEST,
    toolchain: dict[str, str] | None = None,
) -> md.RunResults:
    return md.RunResults(
        definitions=definitions,
        head_sha=head_sha,
        tree_oid=tree_oid,
        external_revisions={"apps": PARTNER} if partners is None else partners,
        needs=needs,
        toolchain=dict(TOOLCHAIN) if toolchain is None else toolchain,
        evidence_digests={"junit": digest},
    )


def _trusted_run(needs: str = ALL_GREEN, **changes: Any) -> md.RunResults:
    changes.setdefault("definitions", BASE)
    changes.setdefault("digest", TRUSTED_DIGEST)
    return _run(needs, **changes)


def _candidate(partner: str | None = PARTNER) -> md.Candidate:
    return md.Candidate(HEAD, TREE, {"apps": partner} if partner else {})


UNCHANGED = trust.Comparison(base=BASE, head=HEAD, changes=())
CHANGED = trust.Comparison(
    base=BASE, head=HEAD, changes=(trust.Change("tools/ci/verdict.py", "modified"),)
)


def _decide(
    own: md.RunResults | None = None,
    *,
    plan: tuple[str, ...] = PLAN,
    comparison: trust.Comparison = UNCHANGED,
    identity: md.Candidate | None = None,
    trusted: md.RunResults | None = None,
) -> md.Decision:
    return md.decide(
        plan,
        own,
        comparison,
        _candidate() if identity is None else identity,
        trusted,
        controller_revision=CONTROLLER,
        git_dir=_MAIN_REPO[0],
        main="main",
    )


#: The planted repository whose ``main`` is ``OLDER`` then ``BASE``, and a commit off main in it
#: (BASE's child on a branch main never reaches); both set by the ``main_repo`` fixture.
_MAIN_REPO: list[Path] = []
OFF_MAIN: list[str] = []


def _stamped_git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_AUTHOR_DATE="1700000000 +0000",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_COMMITTER_DATE="1700000000 +0000",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return proc.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def main_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp("main-repo")
    _stamped_git(repo, "init", "-q", "--object-format=sha1", "-b", "main")
    older = _stamped_git(repo, "commit-tree", _EMPTY_TREE, "-m", "older")
    assert older == OLDER, "the planted older commit is not the one the tables name"
    made = _stamped_git(repo, "commit-tree", _EMPTY_TREE, "-p", older, "-m", "base")
    assert made == BASE, "the planted main commit is not the one the tables name"
    _stamped_git(repo, "update-ref", "refs/heads/main", made)
    side = _stamped_git(repo, "commit-tree", _EMPTY_TREE, "-p", made, "-m", "off main")
    _stamped_git(repo, "update-ref", "refs/heads/side", side)
    _MAIN_REPO[:] = [repo]
    OFF_MAIN[:] = [side]
    return repo


def test_it_imports_only_the_standard_library_and_its_siblings() -> None:
    """A trusted job runs it on the runner image's own python3, before anything is installed."""
    tree = ast.parse(Path(md.__file__).read_text(encoding="utf-8"))
    absolute = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    # ``from .x import y`` names x as its module; ``from . import x`` names x as an alias.
    relative = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
        for name in ([node.module] if node.module else [a.name for a in node.names])
    }
    absolute.discard("__future__")
    assert absolute, "the scan read no imports"
    assert absolute <= set(sys.stdlib_module_names), absolute - set(sys.stdlib_module_names)
    assert relative <= {"catalogue", "merge_verdict", "trust", "verdict"}, relative


def test_it_reads_results_through_the_one_needs_reader() -> None:
    """No second reader of ``toJSON(needs)``, and no second verdict vocabulary."""
    assert md.verdict is verdict
    assert md.from_needs is verdict.from_needs
    assert (md.GREEN, md.RED, md.PENDING) == ("green", "red", "pending")


# ── Green, and its receipt ────────────────────────────────────────────────────────────────────


def test_every_planned_job_green_on_this_candidate_is_green() -> None:
    decision = _decide(_run())
    assert decision.state == md.GREEN, decision.reason
    assert decision.counted == "candidate"
    assert decision.receipt == trust.Receipt(
        controller_revision=CONTROLLER,
        head_sha=HEAD,
        tree_oid=TREE,
        external_revisions={"apps": PARTNER},
        toolchain=TOOLCHAIN,
        evidence_digests={"junit": DIGEST},
    )


def test_the_green_output_is_written_with_the_records_one_format() -> None:
    decision = _decide(_run())
    assert decision.receipt is not None
    assert (
        decision.output["text"]
        == mv.render_output(decision.receipt, title="t", summary="s")["text"]
    )
    assert mv.read_receipt({"output": decision.output}) == decision.receipt
    assert decision.output["title"] and decision.output["summary"]


def test_a_green_without_toolchain_or_evidence_is_red_not_a_green_without_a_receipt() -> None:
    rows: list[tuple[str, md.RunResults, str]] = [
        ("no toolchain", _run(toolchain={}), "toolchain"),
        ("no evidence", md.RunResults(**{**_run().__dict__, "evidence_digests": {}}), "evidence"),
    ]
    assert len(rows) == 2
    for name, run, word in rows:
        decision = _decide(run)
        assert decision.state == md.RED, name
        assert decision.receipt is None, name
        assert word in decision.reason, (name, decision.reason)


@pytest.mark.parametrize("state", ["red", "pending"])
def test_a_decision_that_is_not_green_carries_no_receipt(state: str) -> None:
    decision = _decide(_run(_needs(rails="failure")) if state == "red" else None)
    assert decision.state == state
    assert decision.receipt is None
    with pytest.raises(trust.Refused, match="no receipt"):
        mv.read_receipt({"output": decision.output})
    assert decision.reason in decision.output["summary"]
    assert decision.output["title"] == f"{mv.CHECK_NAME}: {state}"


# ── Red: every way a planned job falls short ──────────────────────────────────────────────────

RED_ROWS: list[tuple[str, tuple[str, ...], str, str, str]] = [
    (
        "a planned job the candidate's workflow dropped",
        PLAN,
        _needs(rails="success", web_unit="success"),
        "python-suite",
        "missing",
    ),
    ("every planned job dropped", PLAN, _needs(), "rails", "missing"),
    (
        "a cancelled planned job",
        PLAN,
        _needs(rails="success", python_suite="cancelled", web_unit="success"),
        "python-suite",
        "cancelled",
    ),
    (
        "a skipped planned job",
        PLAN,
        _needs(rails="skipped", python_suite="success", web_unit="success"),
        "rails",
        "skipped",
    ),
    (
        "a red planned job",
        PLAN,
        _needs(rails="success", python_suite="success", web_unit="failure"),
        "web-unit",
        "failure",
    ),
    (
        "a result word GitHub never gives",
        PLAN,
        _needs(rails="success", python_suite="neutral", web_unit="success"),
        "python-suite",
        "unknown result",
    ),
    (
        "a failed job outside the plan",
        PLAN,
        _needs(rails="success", python_suite="success", web_unit="success", extra="failure"),
        "extra",
        "unplanned failure",
    ),
    ("an empty plan", (), ALL_GREEN, "", "empty plan"),
]


def test_every_way_a_planned_job_falls_short_is_red(subtests: Any) -> None:
    assert len(RED_ROWS) == 8, "a red row was dropped from the table"
    for name, plan, needs, key, kind in RED_ROWS:
        with row(subtests, case=name):
            decision = _decide(_run(needs), plan=plan)
            assert decision.state == md.RED, decision.reason
            assert any(r.key == key and r.kind == kind for r in decision.reasons), decision.reasons
            assert decision.receipt is None


# ── Red: main's catalogue check, required beside the plan ─────────────────────────────────────

#: Each way the catalogue check's job falls short, with the kind its reason must name.
CHECK_RED_ROWS: list[tuple[str, str | None, str]] = [
    ("main's copy refused the candidate's catalogue", "failure", "failure"),
    ("no catalogue check in the run", None, "missing"),
    ("a skipped catalogue check", "skipped", "skipped"),
    ("a cancelled catalogue check", "cancelled", "cancelled"),
]


def test_the_required_check_is_the_job_ci_runs_it_in() -> None:
    assert md.CATALOGUE_CHECK == CHECK_JOB


def test_a_catalogue_check_that_did_not_pass_is_red_with_every_planned_job_green(
    subtests: Any,
) -> None:
    """A refused catalogue check reds the decision, not only its run's conclusion, and a missing,
    skipped or cancelled one is red too: never read as no objection. No plan names the job, so
    the decision requires it of whichever run it counts."""
    assert len(CHECK_RED_ROWS) == 4, "a red row was dropped from the table"
    for name, result, kind in CHECK_RED_ROWS:
        needs = _needs(
            rails="success", python_suite="success", web_unit="success", catalogue_check=result
        )
        for counted in (md.CANDIDATE_RUN, md.DEFAULT_BRANCH_RUN):
            with row(subtests, case=f"{name}, the {counted} run counted"):
                if counted == md.CANDIDATE_RUN:
                    decision = _decide(_run(needs))
                else:
                    decision = _decide(_run(), comparison=CHANGED, trusted=_trusted_run(needs))
                assert decision.state == md.RED, decision.reason
                assert decision.counted == counted
                kinds = {(r.key, r.kind) for r in decision.reasons}
                assert kinds == {(CHECK_JOB, kind)}, decision.reasons
                assert decision.receipt is None


def test_needs_that_cannot_be_read_is_red(subtests: Any) -> None:
    rows: list[tuple[str, str]] = [
        ("not JSON", "{"),
        (
            "a key given twice",
            '{"rails": {"result": "failure", "outputs": {}}, '
            '"rails": {"result": "success", "outputs": {}}}',
        ),
        ("not an object of jobs", "[]"),
        ("a job without outputs", '{"rails": {"result": "success"}}'),
    ]
    assert len(rows) == 4, "a refusal was dropped from the table"
    for name, needs in rows:
        with row(subtests, case=name):
            decision = _decide(_run(needs))
            assert decision.state == md.RED, decision.reason
            assert "results cannot be read" in decision.reason


def test_needs_that_is_not_text_is_refused() -> None:
    """``toJSON(needs)`` is text; anything else is refused where the run is handed in."""
    with pytest.raises(trust.Refused, match="needs"):
        _run({"rails": {"result": "success", "outputs": {}}})  # type: ignore[arg-type]


def test_a_plan_that_cannot_be_read_is_red_and_named_as_the_plan() -> None:
    """A malformed plan is the plan's fault, not the run's: the reason must not blame results."""
    decision = _decide(_run(), plan=("rails", 3))  # type: ignore[arg-type]
    assert decision.state == md.RED, decision.reason
    assert "plan" in decision.reason, decision.reason
    assert "results cannot be read" not in decision.reason, decision.reason
    assert decision.receipt is None


def test_a_run_naming_this_head_with_another_tree_is_red(subtests: Any) -> None:
    """A head has one tree; a run that says otherwise is not believed, whichever run counted.
    It is red before the partner check: a moved partner does not soften it to pending."""
    rows: list[tuple[str, md.Decision, str]] = [
        ("the candidate's own run", _decide(_run(tree_oid=OTHER_TREE)), "candidate"),
        (
            "the default branch's run",
            _decide(_run(), comparison=CHANGED, trusted=_trusted_run(tree_oid=OTHER_TREE)),
            "default branch",
        ),
        (
            "another tree, and a partner that moved since",
            _decide(_run(tree_oid=OTHER_TREE), identity=_candidate(NEW_PARTNER)),
            "candidate",
        ),
    ]
    assert len(rows) == 3, "a tree row was dropped from the table"
    for name, decision, counted in rows:
        with row(subtests, case=name):
            assert decision.state == md.RED, decision.reason
            assert decision.counted == counted
            assert decision.receipt is None
            assert OTHER_TREE in decision.reason, decision.reason


def test_a_comparison_whose_base_is_not_mains_commit_is_red(subtests: Any) -> None:
    """A base that is not main's tip must never pick which run counts: not with a green own run,
    and not with a green run of that base's definitions. An older commit on main is not main's
    commit either: main may have changed a definition since."""
    rows: list[tuple[str, str, str]] = [
        ("a commit off main", OFF_MAIN[0], "not on main"),
        ("an older commit on main", OLDER, "not its tip"),
        ("an id that is no commit here", "9" * 40, "not a commit"),
        ("a branch name", "main", "full commit id"),
        ("a short id", BASE[:12], "full commit id"),
        ("an id with a trailing newline", BASE + "\n", "full commit id"),
    ]
    assert len(rows) == 6, "a base row was dropped from the table"
    for name, base, match in rows:
        with row(subtests, case=name):
            for changes in ((), CHANGED.changes):
                comparison = trust.Comparison(base=base, head=HEAD, changes=changes)
                # A full id can name the definitions of a run handed in beside it.
                full = re.fullmatch("[0-9a-f]{40}", base)
                trusted = _trusted_run(definitions=base) if full else None
                decision = _decide(_run(), comparison=comparison, trusted=trusted)
                assert decision.state == md.RED, (changes, decision.reason)
                assert "base is not the default branch's commit" in decision.reason
                assert match in decision.reason, decision.reason
                assert decision.receipt is None
    # The positive control: main's own commit as the base is believed.
    assert _decide(_run()).state == md.GREEN


def test_a_prs_cancelled_planned_job_is_red_and_never_superseded() -> None:
    """A pull request is read with ``verdict.decide``, not ``decide_main_push``: a cancelled job
    stays cancelled, and nothing names a newer commit as having replaced it."""
    decision = _decide(_run(_needs(rails="success", python_suite="cancelled", web_unit="success")))
    assert decision.state == md.RED
    kinds = {(r.key, r.kind) for r in decision.reasons}
    assert kinds == {("python-suite", verdict.CANCELLED)}, kinds
    assert all(r.kind != verdict.SUPERSEDED for r in decision.reasons)
    assert "superseded" not in decision.output["summary"] + decision.output["text"]
    assert not hasattr(decision, "superseded_by")


def test_a_comparison_of_another_head_is_red() -> None:
    other = trust.Comparison(base=BASE, head=OLD_HEAD, changes=())
    decision = _decide(_run(), comparison=other)
    assert decision.state == md.RED
    assert "comparison" in decision.reason


# ── Pending: nothing counted for this candidate yet ───────────────────────────────────────────

PENDING_ROWS: list[
    tuple[str, md.RunResults | None, trust.Comparison, md.RunResults | None, str]
] = [
    ("no run reported yet", None, UNCHANGED, None, "candidate's run"),
    (
        "a run of an older head, on its own tree",
        _run(head_sha=OLD_HEAD, definitions=OLD_HEAD, tree_oid=OLD_TREE),
        UNCHANGED,
        None,
        "older head",
    ),
    (
        "a run of other definitions than the head's",
        _run(definitions=OLD_HEAD),
        UNCHANGED,
        None,
        "definitions",
    ),
    (
        "a changed definition, and no default-branch run yet",
        _run(),
        CHANGED,
        None,
        "default branch's",
    ),
    (
        "a changed definition, and a run of other definitions",
        _run(),
        CHANGED,
        _trusted_run(definitions=OLD_HEAD),
        "definitions",
    ),
    (
        "a changed definition, and the default-branch run of an older head",
        _run(),
        CHANGED,
        _trusted_run(head_sha=OLD_HEAD, tree_oid=OLD_TREE),
        "older head",
    ),
]


def test_a_candidate_with_no_counted_run_for_it_is_pending(subtests: Any) -> None:
    assert len(PENDING_ROWS) == 6, "a pending row was dropped from the table"
    for name, own, comparison, trusted, word in PENDING_ROWS:
        with row(subtests, case=name):
            decision = _decide(own, comparison=comparison, trusted=trusted)
            assert decision.state == md.PENDING, decision.reason
            assert word in decision.reason, decision.reason
            assert decision.receipt is None


# ── The partner ───────────────────────────────────────────────────────────────────────────────


def test_a_changed_partner_head_turns_green_back_to_pending(subtests: Any) -> None:
    run = _run()
    assert _decide(run).state == md.GREEN
    rows: list[tuple[str, md.Candidate]] = [
        ("the partner's head moved", _candidate(NEW_PARTNER)),
        ("the partner was dropped", _candidate(None)),
        (
            "a second partner was named",
            md.Candidate(HEAD, TREE, {"apps": PARTNER, "x": NEW_PARTNER}),
        ),
    ]
    assert len(rows) == 3
    for name, identity in rows:
        with row(subtests, case=name):
            decision = _decide(run, identity=identity)
            assert decision.state == md.PENDING, decision.reason
            assert "partner" in decision.reason
            assert decision.receipt is None


def test_a_run_on_the_new_partner_head_is_green_again() -> None:
    decision = _decide(_run(partners={"apps": NEW_PARTNER}), identity=_candidate(NEW_PARTNER))
    assert decision.state == md.GREEN, decision.reason
    assert decision.receipt is not None
    assert decision.receipt.external_revisions == {"apps": NEW_PARTNER}


def test_a_changed_partner_head_also_takes_back_the_default_branchs_green() -> None:
    trusted = _trusted_run()
    assert _decide(_run(), comparison=CHANGED, trusted=trusted).state == md.GREEN
    moved = _decide(_run(), comparison=CHANGED, trusted=trusted, identity=_candidate(NEW_PARTNER))
    assert moved.state == md.PENDING


# ── Definitions: a changed one sends the verdict to the default branch's run ──────────────────

_WORKFLOW = """\
name: CI
on:
  pull_request:
jobs:
  rails:
    runs-on: ubuntu-24.04
    steps:
      - run: make rails
  python-suite:
    runs-on: ubuntu-24.04
    steps:
      - run: make test
  web-unit:
    runs-on: ubuntu-24.04
    steps:
      - run: make web
"""

_BASE_FILES = {
    ".github/workflows/ci.yml": _WORKFLOW,
    "tools/ci/__init__.py": "",
    "tools/ci/verdict.py": "def verdict(results):\n    return all(results)\n",
    "Makefile": "rails:\n\tpython -m pytest tests/rails\n\ntest:\n\tpython -m pytest\n\n"
    "web:\n\tnpm test\n",
    "src/app.py": "print('product code')\n",
    "README.md": "readme\n",
}


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return proc.stdout.strip()


def _write(repo: Path, files: dict[str, str] | dict[str, str | bytes]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _planted(tmp_path: Path, files: dict[str, str] | dict[str, str | bytes]) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, files)
    base = _commit(repo, "base")
    _git(repo, "switch", "-q", "-c", "candidate")
    return repo, base


#: The three definition edits the issue names, each one that would let a candidate's own run
#: pass while its planned jobs keep their names, and the no-change positive control.
DEFINITION_ROWS: list[tuple[str, dict[str, str], bool]] = [
    (
        "an always-pass job kept by name",
        {".github/workflows/ci.yml": _WORKFLOW.replace("make test", "true")},
        True,
    ),
    (
        "an edited verdict.py",
        {"tools/ci/verdict.py": "def verdict(results):\n    return True\n"},
        True,
    ),
    (
        "an emptied Makefile target",
        {"Makefile": _BASE_FILES["Makefile"].replace("\tpython -m pytest\n\n", "\ttrue\n\n")},
        True,
    ),
    ("no definition changed", {"src/app.py": "print('changed')\n", "README.md": "new\n"}, False),
]


def test_a_definition_change_is_judged_by_the_default_branchs_run(
    tmp_path: Path, subtests: Any
) -> None:
    assert len(DEFINITION_ROWS) == 4, "a definition row was dropped from the table"
    assert sum(changed for *_, changed in DEFINITION_ROWS) == 3
    for n, (name, edit, changed) in enumerate(DEFINITION_ROWS):
        with row(subtests, case=name):
            repo, base = _planted(tmp_path / str(n), dict(_BASE_FILES))
            _write(repo, edit)
            head = _commit(repo, "candidate")
            tree = _git(repo, "rev-parse", f"{head}^{{tree}}")
            comparison = trust.compare(repo, base=base, head=head)
            assert comparison.trusted is not changed, comparison.changes
            identity = md.Candidate(head, tree, {})

            def run(needs: str, definitions: str) -> md.RunResults:
                # Each run carries its own evidence and toolchain, so a receipt names its source.
                own = definitions == head
                return _run(
                    needs,
                    definitions=definitions,
                    head_sha=head,
                    tree_oid=tree,
                    partners={},
                    digest=DIGEST if own else TRUSTED_DIGEST,
                    toolchain=dict(OWN_TOOLCHAIN if own else TOOLCHAIN),
                )

            own_green = run(ALL_GREEN, head)
            main_red = run(
                _needs(rails="success", python_suite="failure", web_unit="success"), base
            )
            main_green = run(ALL_GREEN, base)

            def decide(own: md.RunResults | None, trusted: md.RunResults | None) -> md.Decision:
                return md.decide(
                    PLAN,
                    own,
                    comparison,
                    identity,
                    trusted,
                    controller_revision=CONTROLLER,
                    git_dir=repo,
                    main="main",
                )

            if changed:
                # The candidate's own green is advisory: alone it decides nothing.
                assert decide(own_green, None).state == md.PENDING
                red = decide(own_green, main_red)
                assert red.state == md.RED, red.reason
                assert red.counted == "default branch"
                assert any(r.key == "python-suite" for r in red.reasons)
                green = decide(run(_needs(rails="failure"), head), main_green)
                assert green.state == md.GREEN, green.reason
                assert green.receipt is not None
                assert green.receipt.evidence_digests == {"junit": TRUSTED_DIGEST}
                assert green.receipt.toolchain == TOOLCHAIN
            else:
                green = decide(own_green, None)
                assert green.state == md.GREEN, green.reason
                assert green.counted == "candidate"
                assert green.receipt is not None
                assert green.receipt.evidence_digests == {"junit": DIGEST}
                assert green.receipt.toolchain == OWN_TOOLCHAIN
                assert decide(run(_needs(rails="failure"), head), None).state == md.RED


#: main's tighter ``verdict.py``: after the base, an empty set of results no longer passes.
_TIGHTENED_VERDICT = "def verdict(results):\n    return bool(results) and all(results)\n"


def test_a_definition_main_changed_and_the_candidate_put_back_is_judged_by_mains_tip(
    tmp_path: Path,
) -> None:
    """main tightens ``verdict.py`` after an older commit, and the candidate puts the old copy
    back. Against the older commit the candidate changed nothing; against main's tip it did. Only
    main's tip is the default branch's commit, so the older one never lets its own run count."""
    repo, older = _planted(tmp_path, dict(_BASE_FILES))
    _git(repo, "switch", "-q", "main")
    _write(repo, {"tools/ci/verdict.py": _TIGHTENED_VERDICT})
    tip = _commit(repo, "main tightens verdict.py")
    _git(repo, "switch", "-q", "-C", "candidate")
    _write(repo, {"tools/ci/verdict.py": _BASE_FILES["tools/ci/verdict.py"]})
    head = _commit(repo, "candidate puts the old verdict.py back")
    tree = _git(repo, "rev-parse", f"{head}^{{tree}}")
    assert _git(repo, "rev-parse", "main") == tip
    stale = trust.compare(repo, base=older, head=head)
    fresh = trust.compare(repo, base=tip, head=head)
    assert stale.trusted, stale.changes
    assert [c.path for c in fresh.changes] == ["tools/ci/verdict.py"]
    identity = md.Candidate(head, tree, {})

    def run(definitions: str) -> md.RunResults:
        return _run(definitions=definitions, head_sha=head, tree_oid=tree, partners={})

    def decide(
        comparison: trust.Comparison, own: md.RunResults, trusted: md.RunResults | None
    ) -> md.Decision:
        return md.decide(
            PLAN,
            own,
            comparison,
            identity,
            trusted,
            controller_revision=CONTROLLER,
            git_dir=repo,
            main="main",
        )

    for trusted in (None, run(older)):
        decision = decide(stale, run(head), trusted)
        assert decision.state == md.RED, decision.reason
        assert "not its tip" in decision.reason, decision.reason
        assert decision.counted is None
        assert decision.receipt is None
    assert decide(fresh, run(head), None).state == md.PENDING
    green = decide(fresh, run(head), run(tip))
    assert green.state == md.GREEN, green.reason
    assert green.counted == "default branch"


def test_with_no_definition_changed_the_candidates_own_run_decides() -> None:
    """A default-branch run handed in beside it changes nothing: the comparison chose."""
    red_trusted = _trusted_run(_needs(rails="failure"))
    assert _decide(_run(), trusted=red_trusted).state == md.GREEN
    assert _decide(_run(_needs(rails="failure")), trusted=_trusted_run()).state == md.RED


# ── The planned set comes from the default branch's catalogue ─────────────────────────────────

_CATALOGUE = """
[tier.merge]
jobs = ["rails", "python-suite", "python-suite-macos", "security", "docs-check"]

[[job]]
id = "rails"
tiers = ["change", "merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
reuse = "tree"
timeout_min = 10
command = [{ argv = ["uv", "run", "pytest", "tests/rails"] }]

[[job]]
id = "python-suite"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 55
shards = 4
command = [{ argv = ["uv", "run", "pytest", "--shard", "{shard}/4"] }]

[[job]]
id = "python-suite-macos"
tiers = ["merge"]
inputs = ["src/", "tests/"]
external_inputs = ["shard-history"]
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
platform = "macos-26"
timeout_min = 50
command = [{ argv = ["uv", "run", "pytest", "tests"] }]

[[job]]
id = "security"
tiers = ["merge"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "security"
timeout_min = 25
command = [{ argv = ["uv", "run", "pytest", "tests/security"] }]

[[job]]
id = "docs-check"
tiers = ["change", "merge"]
inputs = ["docs/"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
timeout_min = 5
command = [{ argv = ["uv", "run", "python", "scripts/docs_check.py"] }]

[[job]]
id = "lint-fast"
tiers = ["change"]
inputs = [":(glob)**"]
external_inputs = []
toolchain = ["uv-sync"]
cache = ["uv"]
security = "ordinary"
timeout_min = 5
command = [{ argv = ["uv", "run", "flake8"] }]
"""

MERGE_TIER = ("rails", "python-suite", "python-suite-macos", "security", "docs-check")
#: The catalogue with docs-check dropped from the merge tier: both edits a candidate needs, its
#: tiers and the list. The loader accepts it, so only where it is read keeps it out of a plan.
_SHRUNK_CATALOGUE = _CATALOGUE.replace(
    'id = "docs-check"\ntiers = ["change", "merge"]', 'id = "docs-check"\ntiers = ["change"]'
).replace(', "docs-check"]', "]")


@pytest.fixture
def with_catalogue(tmp_path: Path) -> tuple[Path, str]:
    assert catalogue.CATALOGUE == "tools/ci/catalogue.toml"
    return _planted(tmp_path, {catalogue.CATALOGUE: _CATALOGUE, "README.md": "r\n"})


def test_the_planned_set_is_the_default_branchs_merge_tier(
    with_catalogue: tuple[Path, str],
) -> None:
    repo, base = with_catalogue
    assert md.planned_jobs(repo, base, main="main") == MERGE_TIER


def test_the_planned_set_is_read_at_mains_tip_only(tmp_path: Path) -> None:
    """main grew its merge tier after an older commit: that commit's catalogue plans too little."""
    repo, older = _planted(tmp_path, {catalogue.CATALOGUE: _SHRUNK_CATALOGUE, "README.md": "r\n"})
    _git(repo, "switch", "-q", "main")
    _write(repo, {catalogue.CATALOGUE: _CATALOGUE})
    tip = _commit(repo, "main adds docs-check to the merge tier")
    with pytest.raises(trust.Refused, match="not its tip"):
        md.planned_jobs(repo, older, main="main")
    assert md.planned_jobs(repo, tip, main="main") == MERGE_TIER


def test_no_exported_git_variable_redirects_what_is_read(
    with_catalogue: tuple[Path, str], tmp_path: Path, subtests: Any
) -> None:
    """A GIT_DIR, object store or index a calling hook exported names another repository, whose
    main is elsewhere and whose catalogue plans less; neither the plan nor the base check reads it.
    """
    repo, base = with_catalogue
    elsewhere, _ = _planted(tmp_path / "elsewhere", {catalogue.CATALOGUE: _SHRUNK_CATALOGUE})
    exports = {
        "GIT_DIR": str(elsewhere / ".git"),
        "GIT_OBJECT_DIRECTORY": str(elsewhere / ".git" / "objects"),
        "GIT_INDEX_FILE": str(elsewhere / ".git" / "index"),
    }
    assert len(exports) == 3
    for name, value in exports.items():
        with row(subtests, export=name), pytest.MonkeyPatch.context() as env:
            env.setenv(name, value)
            assert md.planned_jobs(repo, base, main="main") == MERGE_TIER
            assert _decide(_run()).state == md.GREEN


def test_the_candidates_own_catalogue_never_sets_the_plan(
    with_catalogue: tuple[Path, str], subtests: Any
) -> None:
    """A candidate that drops a job from its catalogue, its commit, or the worktree it leaves
    behind, cannot shrink the plan: only main's tip is read, and only its blob."""
    repo, base = with_catalogue
    shrunk = _SHRUNK_CATALOGUE
    assert shrunk.count('"docs-check"') == _CATALOGUE.count('"docs-check"') - 1
    assert [j.id for j in catalogue.load(shrunk).tier("merge")] == list(MERGE_TIER[:-1])
    _write(repo, {catalogue.CATALOGUE: shrunk})
    head = _commit(repo, "candidate drops docs-check from the merge tier")
    rows = [
        ("the candidate's commit", lambda: md.planned_jobs(repo, head, main="main"), "not on main"),
        (
            "a branch name for a commit",
            lambda: md.planned_jobs(repo, "candidate", main="main"),
            "full commit id",
        ),
        ("a short id", lambda: md.planned_jobs(repo, base[:12], main="main"), "full commit id"),
    ]
    assert len(rows) == 3
    for name, call, match in rows:
        with row(subtests, case=name):
            with pytest.raises(trust.Refused, match=match):
                call()
    # The worktree holds the candidate's shrunk catalogue; main's commit still plans all four.
    assert (repo / catalogue.CATALOGUE).read_text(encoding="utf-8") == shrunk
    plan = md.planned_jobs(repo, base, main="main")
    assert plan == MERGE_TIER
    # The candidate's workflow runs neither docs-check, the job its catalogue dropped, nor
    # python-suite nor security: all three are planned, so the verdict reads each as missing.
    decision = md.decide(
        plan,
        _run(_needs(rails="success", python_suite_macos="success"), head_sha=HEAD),
        UNCHANGED,
        _candidate(),
        None,
        controller_revision=CONTROLLER,
        git_dir=_MAIN_REPO[0],
        main="main",
    )
    assert decision.state == md.RED
    missing = {r.key for r in decision.reasons if r.kind == "missing"}
    assert missing == {"docs-check", "python-suite", "security"}, decision.reasons


def test_a_default_branch_without_a_readable_catalogue_plans_nothing(
    tmp_path: Path, subtests: Any
) -> None:
    rows: list[tuple[str, dict[str, str | bytes], str]] = [
        ("no catalogue", {"README.md": "r\n"}, re.escape(f"has no {catalogue.CATALOGUE}")),
        ("a catalogue that is not UTF-8", {catalogue.CATALOGUE: b"\xff\xfe[[job]]\n"}, "not UTF-8"),
        ("a catalogue that is not TOML", {catalogue.CATALOGUE: "[[job]\n"}, "TOML"),
        (
            "a catalogue its own rules refuse",
            {catalogue.CATALOGUE: _CATALOGUE.replace('id = "rails"', 'id = "rails-renamed"')},
            "rails",
        ),
    ]
    assert len(rows) == 4
    for n, (name, files, match) in enumerate(rows):
        with row(subtests, case=name):
            repo, base = _planted(tmp_path / str(n), files)
            with pytest.raises(trust.Refused, match=match):
                md.planned_jobs(repo, base, main="main")


# ── What the caller hands in ──────────────────────────────────────────────────────────────────


def test_an_identity_that_names_nothing_exactly_is_refused(subtests: Any) -> None:
    rows: list[tuple[str, dict[str, Any]]] = [
        ("a head with a trailing newline", {"head_sha": HEAD + "\n"}),
        ("a short head", {"head_sha": HEAD[:39]}),
        ("an uppercase tree", {"tree_oid": TREE.upper()}),
        ("a tree with a trailing newline", {"tree_oid": TREE + "\n"}),
        ("a partner that is no id", {"external_revisions": {"apps": "main"}}),
        ("an unnamed partner", {"external_revisions": {"": PARTNER}}),
    ]
    assert len(rows) == 6
    good = {"head_sha": HEAD, "tree_oid": TREE, "external_revisions": {"apps": PARTNER}}
    for name, change in rows:
        with row(subtests, case=name):
            with pytest.raises(trust.Refused):
                md.Candidate(**{**good, **change})
            with pytest.raises(trust.Refused):
                _run(
                    **{
                        "head_sha": change.get("head_sha", HEAD),
                        "tree_oid": change.get("tree_oid", TREE),
                        "partners": change.get("external_revisions", {"apps": PARTNER}),
                    }
                )


def test_sha256_ids_are_full_ids_too() -> None:
    """A repository in SHA-256 names its objects with 64 hex digits; they are full ids."""
    head, tree, partner = "a" * 64, "b" * 64, "d" * 64
    identity = md.Candidate(head, tree, {"apps": partner})
    run = _run(definitions=head, head_sha=head, tree_oid=tree, partners={"apps": partner})
    comparison = trust.Comparison(base=BASE, head=head, changes=())
    decision = _decide(run, comparison=comparison, identity=identity)
    assert decision.state == md.GREEN, decision.reason
    assert decision.receipt is not None
    assert (decision.receipt.head_sha, decision.receipt.tree_oid) == (head, tree)
    assert decision.receipt.external_revisions == {"apps": partner}


def test_a_run_whose_definitions_are_no_id_is_refused() -> None:
    with pytest.raises(trust.Refused):
        _run(definitions="main")


def test_a_callers_mapping_changed_later_does_not_change_the_decision(subtests: Any) -> None:
    partners = {"apps": PARTNER}
    identity = md.Candidate(HEAD, TREE, partners)
    partners["apps"] = NEW_PARTNER
    assert _decide(_run(), identity=identity).state == md.GREEN
    # The run's mappings too: what it ran against, and what its receipt would name.
    expected = _decide(_run()).receipt
    assert expected is not None
    rows: list[tuple[str, str, str, str]] = [
        ("the run's partners", "external_revisions", "apps", NEW_PARTNER),
        ("the run's toolchain", "toolchain", "python", "0.0.0"),
        ("the run's evidence", "evidence_digests", "junit", TRUSTED_DIGEST),
    ]
    assert len(rows) == 3
    for name, field, key, value in rows:
        with row(subtests, case=name):
            given: dict[str, dict[str, str]] = {
                "external_revisions": {"apps": PARTNER},
                "toolchain": dict(TOOLCHAIN),
                "evidence_digests": {"junit": DIGEST},
            }
            run = md.RunResults(
                definitions=HEAD, head_sha=HEAD, tree_oid=TREE, needs=ALL_GREEN, **given
            )
            given[field][key] = value
            decision = _decide(run)
            assert decision.state == md.GREEN, decision.reason
            assert decision.receipt == expected
