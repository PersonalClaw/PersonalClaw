"""The CI trust boundary (``tools/ci/trust.py``), on planted repositories.

Each case builds a small git repository with a ``main`` branch and a candidate branch, so what
is compared is real git objects, the way a trusted job reads them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.ci import trust

REPO = Path(__file__).resolve().parents[1]

_WORKFLOW = """\
name: CI
on:
  pull_request:
permissions:
  contents: read
jobs:
  lint:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
      - run: make lint
      - run: python3 scripts/classify.py < /tmp/changed.txt
      - run: uv run python -m harness validate
      - name: web
        working-directory: web
        run: node e2e/assert.mjs report.json
"""

_BASE_FILES = {
    ".github/workflows/ci.yml": _WORKFLOW,
    "tools/ci/__init__.py": "",
    "tools/ci/verdict.py": "def verdict(results):\n    return all(results)\n",
    "tools/ci/catalogue.toml": (
        '[job.lint]\ncommand = "make lint"\n\n[job.test]\ncommand = "make test"\n'
    ),
    "Makefile": "lint:\n\tpython -m black --check src\n\ntest:\n\tpython -m pytest\n",
    "scripts/classify.py": "print('frontend=true')\n",
    "scripts/unrelated.py": "print('not run by CI')\n",
    "harness/__main__.py": "print('validate')\n",
    "harness/helper.py": "X = 1\n",
    "web/e2e/assert.mjs": "process.exit(0)\n",
    "src/app.py": "print('product code')\n",
    "tests/test_app.py": "def test_it():\n    pass\n",
    "tests/test_ci_contract.py": "ROWS = ()\n",
    "README.md": "readme\n",
}

_HELPERS = {"harness/__main__.py": ("harness/",)}


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


def _write(repo: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, dict(_BASE_FILES))
    _commit(repo, "base")
    _git(repo, "switch", "-q", "-c", "candidate")
    return repo


def _candidate(repo: Path, files: dict[str, str | None]) -> trust.Comparison:
    _write(repo, files)
    head = _commit(repo, "candidate")
    return trust.compare(repo, base="main", head=head, helpers=_HELPERS)


# ── The definitions check ─────────────────────────────────────────────────────────────────────


def test_a_candidate_that_changes_no_definition_counts_its_own_run(planted: Path) -> None:
    found = _candidate(
        planted,
        {
            "src/app.py": "print('changed product code')\n",
            "README.md": "changed\n",
            "scripts/unrelated.py": "print('still not run by CI')\n",
        },
    )
    assert found.trusted, found.changes


def test_a_changed_test_is_code_under_test_not_a_definition(planted: Path) -> None:
    assert _candidate(planted, {"tests/test_app.py": "def test_it():\n    assert 1\n"}).trusted


def test_a_makefile_no_command_runs_is_not_a_definition(planted: Path) -> None:
    _write(planted, {".github/workflows/ci.yml": _WORKFLOW.replace("make lint", "echo lint")})
    base = _commit(planted, "no make on main either")
    _git(planted, "branch", "-f", "main", base)
    found = _candidate(planted, {"Makefile": "lint:\n\ttrue\n"})
    assert found.trusted, found.changes


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param(
            {".github/workflows/ci.yml": _WORKFLOW.replace("make lint", "true")},
            {(".github/workflows/ci.yml", "modified")},
            id="an edited workflow",
        ),
        pytest.param(
            {".github/workflows/extra.yml": "on: pull_request\njobs: {}\n"},
            {(".github/workflows/extra.yml", "added")},
            id="an added workflow",
        ),
        pytest.param(
            {".github/actions/setup/action.yml": "runs:\n  using: composite\n  steps: []\n"},
            {(".github/actions/setup/action.yml", "added")},
            id="an added local action",
        ),
        pytest.param(
            {"tools/ci/catalogue.toml": '[job.lint]\ncommand = "make lint"\n'},
            {("tools/ci/catalogue.toml", "modified")},
            id="a dropped catalogue entry",
        ),
        pytest.param(
            {"tools/ci/verdict.py": "def verdict(results):\n    return True\n"},
            {("tools/ci/verdict.py", "modified")},
            id="a verdict that passes everything",
        ),
        pytest.param(
            {"tools/ci/__init__.py": None},
            {("tools/ci/__init__.py", "deleted")},
            id="a deleted controller file",
        ),
        pytest.param(
            {"Makefile": "lint:\n\ttrue\n\ntest:\n\tpython -m pytest\n"},
            {("Makefile", "modified")},
            id="an emptied Makefile target a command runs",
        ),
        pytest.param(
            {"scripts/classify.py": "print('frontend=false')\n"},
            {("scripts/classify.py", "modified")},
            id="an edited script a step runs",
        ),
        pytest.param(
            {"web/e2e/assert.mjs": "process.exit(1)\n"},
            {("web/e2e/assert.mjs", "modified")},
            id="a script found through the step's working-directory",
        ),
        pytest.param(
            {"harness/helper.py": "X = 2\n"},
            {("harness/helper.py", "modified")},
            id="a reviewed helper of an entry point",
        ),
        pytest.param(
            {"tests/test_ci_contract.py": "ROWS = None\n"},
            {("tests/test_ci_contract.py", "modified")},
            id="the contract table, the one test that judges",
        ),
    ],
)
def test_a_changed_definition_sends_the_verdict_to_the_default_branch(
    planted: Path, files: dict[str, str | None], expected: set[tuple[str, str]]
) -> None:
    found = _candidate(planted, files)
    assert not found.trusted
    assert {(c.path, c.kind) for c in found.changes} == expected


def test_a_renamed_definition_is_one_rename(planted: Path) -> None:
    text = _BASE_FILES[".github/workflows/ci.yml"]
    found = _candidate(planted, {".github/workflows/ci.yml": None, ".github/workflows/c.yml": text})
    assert found.changes == (
        trust.Change(".github/workflows/c.yml", "renamed", old_path=".github/workflows/ci.yml"),
    )


def _executable(repo: Path, rel: str) -> None:
    _git(repo, "update-index", "--chmod=+x", rel)


def _symlink(repo: Path, rel: str) -> None:
    """The same path, now a symlink whose target is the file's own text, so the id is the same."""
    text = (repo / rel).read_text(encoding="utf-8")
    (repo / rel).unlink()
    os.symlink(text, repo / rel)
    _git(repo, "add", rel)


def _gitlink(repo: Path, rel: str) -> None:
    oid = _git(repo, "rev-parse", f"HEAD:{rel}")
    _git(repo, "rm", "-q", "--cached", rel)
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{oid},{rel}")


@pytest.mark.parametrize(
    ("plant", "mode"),
    [
        pytest.param(_executable, "100755", id="an executable bit"),
        pytest.param(_symlink, "120000", id="a file that becomes a symlink"),
        pytest.param(_gitlink, "160000", id="a file that becomes a gitlink"),
    ],
)
def test_a_definition_whose_mode_alone_changes_is_a_change(
    planted: Path, plant: object, mode: str
) -> None:
    """Content unchanged, mode changed: a run of it runs something else, so it is not trusted."""
    rel = "scripts/classify.py"
    before = _git(planted, "rev-parse", f"main:{rel}")
    assert callable(plant)
    plant(planted, rel)
    _git(planted, "commit", "-q", "-m", "only the mode")
    head = _git(planted, "rev-parse", "HEAD")
    listed = _git(planted, "ls-tree", head, rel).split()
    assert listed[0] == mode and listed[2] == before, "the plant changed more than the mode"
    found = trust.compare(planted, base="main", head=head, helpers=_HELPERS)
    assert found.changes == (trust.Change(rel, "modified"),)


def test_a_move_that_also_changes_the_mode_is_not_a_rename(planted: Path) -> None:
    rel, moved = "scripts/classify.py", "scripts/classify2.py"
    _git(planted, "mv", rel, moved)
    _executable(planted, moved)
    _git(planted, "commit", "-q", "-m", "moved and made executable")
    found = trust.compare(planted, base="main", head="HEAD", helpers=_HELPERS)
    assert {(c.path, c.kind) for c in found.changes} == {(rel, "deleted")}


def test_a_script_whose_command_is_deleted_with_it_is_still_compared(planted: Path) -> None:
    """The head no longer names the script, but the base does, so both changes show."""
    workflow = _WORKFLOW.replace(
        "      - run: python3 scripts/classify.py < /tmp/changed.txt\n", ""
    )
    found = _candidate(planted, {".github/workflows/ci.yml": workflow, "scripts/classify.py": None})
    assert {(c.path, c.kind) for c in found.changes} == {
        (".github/workflows/ci.yml", "modified"),
        ("scripts/classify.py", "deleted"),
    }


def test_the_entry_points_of_each_command_form(planted: Path) -> None:
    found = trust.entry_points(planted, "main", helpers=_HELPERS)
    assert found == {
        "Makefile",
        "scripts/classify.py",
        "harness/__main__.py",
        "harness/helper.py",
        "web/e2e/assert.mjs",
    }


def test_the_real_workflows_name_the_entry_points_they_run(tmp_path: Path) -> None:
    """This repository's workflows, read by the same parser, find the files they really run."""
    repo = tmp_path / "real"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    shutil.copytree(REPO / ".github" / "workflows", repo / ".github" / "workflows")
    expected = {
        "Makefile",
        "package.json",
        "scripts/ci_pr_feedback.mjs",
        "scripts/ci_touches_frontend.py",
        "scripts/ci_touches_sdk.py",
        "scripts/gate_report.py",
        "scripts/apps_sdk_contract.py",
        "scripts/lint_bundled_apps.py",
        "scripts/release_tags.py",
        "scripts/sdk_signature_snapshot.py",
        "tools/browse_live_quarantine_frequency.py",
        "tools/docker_single_container_smoke.py",
        "harness/__main__.py",
        "web/e2e/assert-no-route-skips.mjs",
    }
    _write(repo, {rel: "" for rel in expected})
    _commit(repo, "the real workflows")
    found = trust.entry_points(repo, "main")
    assert expected <= found, sorted(expected - found)


def test_every_reviewed_helper_exists() -> None:
    """A helper that was renamed away would drop out of the comparison in silence."""
    for entry, helpers in trust.REVIEWED_HELPERS.items():
        for rel in (entry, *helpers):
            assert (REPO / rel).exists(), f"{rel} (reviewed for {entry}) is not in the tree"


# ── Receipts and reachability ─────────────────────────────────────────────────────────────────


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _receipt(repo: Path, **overrides: object) -> dict[str, object]:
    head = _git(repo, "rev-parse", "candidate")
    data: dict[str, object] = {
        "controller_revision": _git(repo, "rev-parse", "main"),
        "head_sha": head,
        "tree_oid": _git(repo, "rev-parse", f"{head}^{{tree}}"),
        "external_revisions": {"PersonalClaw/PersonalClawApps": "a" * 40},
        "toolchain": {"python": "3.13.16", "uv": "0.13.1", "node": "24.21.0"},
        "evidence_digests": {"lint": _digest("b"), "test": _digest("c")},
    }
    data.update(overrides)
    return data


@pytest.fixture
def with_candidate(planted: Path) -> Path:
    _candidate(planted, {"src/app.py": "print('candidate')\n"})
    return planted


def test_a_complete_receipt_is_accepted(with_candidate: Path) -> None:
    receipt = trust.check_receipt(with_candidate, _receipt(with_candidate), main="main")
    assert receipt.controller_revision == _git(with_candidate, "rev-parse", "main")


@pytest.mark.parametrize("field", trust.RECEIPT_FIELDS)
def test_a_receipt_without_a_field_is_refused(with_candidate: Path, field: str) -> None:
    data = _receipt(with_candidate)
    del data[field]
    with pytest.raises(trust.Refused, match=field):
        trust.check_receipt(with_candidate, data, main="main")


def test_a_controller_revision_from_the_candidates_own_run_is_refused(with_candidate: Path) -> None:
    own = _git(with_candidate, "rev-parse", "candidate")
    with pytest.raises(trust.Refused, match="is not on main"):
        trust.check_receipt(
            with_candidate, _receipt(with_candidate, controller_revision=own), main="main"
        )


def test_a_tree_that_is_not_the_heads_is_refused(with_candidate: Path) -> None:
    main_tree = _git(with_candidate, "rev-parse", "main^{tree}")
    with pytest.raises(trust.Refused, match="is not the tree of head"):
        trust.check_receipt(
            with_candidate, _receipt(with_candidate, tree_oid=main_tree), main="main"
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"controller_revision": "main"}, "full object id"),
        ({"evidence_digests": {"lint": "md5:abc"}}, "malformed value for lint"),
        ({"evidence_digests": {}}, "no evidence"),
        ({"toolchain": {}}, "no toolchain"),
        ({"external_revisions": {"PersonalClaw/PersonalClawApps": "main"}}, "malformed"),
        ({"head_sha": "f" * 40}, "is not a commit"),
    ],
)
def test_a_malformed_receipt_is_refused(
    with_candidate: Path, overrides: dict[str, object], match: str
) -> None:
    with pytest.raises(trust.Refused, match=match):
        trust.check_receipt(with_candidate, _receipt(with_candidate, **overrides), main="main")


def test_a_receipt_with_no_other_repository_is_accepted(with_candidate: Path) -> None:
    data = _receipt(with_candidate, external_revisions={})
    assert trust.check_receipt(with_candidate, data, main="main").external_revisions == {}


def test_a_commit_on_main_is_reachable(planted: Path) -> None:
    trust.require_on_main(planted, _git(planted, "rev-parse", "main"), main="main")


@pytest.mark.parametrize(
    "commit",
    [
        pytest.param("candidate", id="a branch name stands in for no commit"),
        pytest.param("0" * 40, id="a commit this repository lacks"),
    ],
)
def test_require_on_main_refuses_what_is_not_a_commit(planted: Path, commit: str) -> None:
    with pytest.raises(trust.Refused):
        trust.require_on_main(planted, commit, main="main")


def test_a_commit_that_is_not_an_ancestor_of_main_is_refused(with_candidate: Path) -> None:
    off_main = _git(with_candidate, "rev-parse", "candidate")
    with pytest.raises(trust.Refused, match="is not on main"):
        trust.require_on_main(with_candidate, off_main, main="main")


def test_a_shallow_clone_cannot_show_reachability(planted: Path, tmp_path: Path) -> None:
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth=1", "--branch=main", f"file://{planted}", str(shallow))
    with pytest.raises(trust.Refused, match="shallow"):
        trust.require_on_main(shallow, _git(shallow, "rev-parse", "HEAD"), main="HEAD")
