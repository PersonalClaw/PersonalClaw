"""The source-tree checks of the site baselines (``tests/site_baseline.py``).

Every ``*.sites.jsonl`` in the repository is registered, every registered file exists, and,
against the gate's base commit, no list grew through its file. And the repository's
``.gitattributes`` merges every baseline with git's union driver, so two branches that each add a
line to one merge without a conflict. ``tests/test_site_baseline.py`` holds the checks themselves,
on repositories of its own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import git_helper_guard
from rails_repo import REPO
from site_baseline import UNION_ATTRIBUTE, attribute_problems, check_registry
from table_rows import row

from personalclaw.sdk.testing import neutral_git_env

#: Folders no baseline sits in, besides dot-folders (git's own, the environment, caches).
_SKIPPED = {"node_modules", "__pycache__"}


def _baselines() -> list[Path]:
    """Every ``*.sites.jsonl`` in the checkout, outside dot-folders and installed packages."""
    found: list[Path] = []
    for folder, folders, files in os.walk(REPO):
        folders[:] = [name for name in folders if not name.startswith(".") and name not in _SKIPPED]
        found += [Path(folder) / name for name in files if name.endswith(".sites.jsonl")]
    return sorted(found)


def test_every_site_baseline_in_the_repository_is_registered():
    check_registry(_baselines())


def _planted(tmp_path: Path, attributes: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    env = os.environ | neutral_git_env(git_helper_guard.own_global_config())
    subprocess.run(["git", "-C", str(repo), "init", "-q"], env=env, check=True)
    (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    return repo


#: ``.gitattributes`` that fail to merge a baseline with the union driver, and what is said.
PLANTED = [
    ("no union line", "*.png binary\n", f"does not hold the line `{UNION_ATTRIBUTE}`"),
    (
        "a changed glob",
        "tests/rails/baselines/*.sites.jsonl merge=union\n",
        "git merges web/src/probe.sites.jsonl with merge=unspecified, not union",
    ),
    (
        "a later line that overrides it",
        f"{UNION_ATTRIBUTE}\n*.jsonl -merge\n",
        "git merges tests/rails/baselines/probe.sites.jsonl with merge=unset, not union",
    ),
]


def test_the_repository_merges_every_site_baseline_with_the_union_driver(tmp_path: Path, subtests):
    """The real ``.gitattributes`` holds the line exactly and git answers ``union`` for a
    baseline; each planted file that breaks it fails, by what it broke."""
    assert attribute_problems(REPO) == []
    assert len(PLANTED) == 3, "a planted case was dropped from the table"
    for name, attributes, message in PLANTED:
        with row(subtests, case=name):
            problems = attribute_problems(_planted(tmp_path / name.replace(" ", "-"), attributes))
            assert any(message in problem for problem in problems), problems
