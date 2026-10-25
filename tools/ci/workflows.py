"""The workflows a candidate's checks run in, named in one place (#3915).

``pr.yml`` (:data:`FEEDBACK`, the change tier) runs on every pull request, and ``merge.yml``
(:data:`MERGE`, the merge tier and the push stage) on every pull request and every push to main.
They took ``ci.yml``'s triggers when it was deleted. Each runtime reader of a candidate's runs
names the workflows through this module, and
``tests/rails/test_the_candidate_workflows_are_named_in_one_place_tree.py`` fails any file that
names the deleted workflow in code.

Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

#: The change tier's quick jobs, on every pull request.
FEEDBACK = "pr.yml"
#: The merge tier on every pull request, and the push stage on every push to main.
MERGE = "merge.yml"

#: Each workflow by its ``name:``, the name a ``workflow_run`` listener follows it by.
NAMES: dict[str, str] = {FEEDBACK: "PR", MERGE: "Merge"}

#: The workflows whose push to main is that commit's run.
MAIN_PUSH: tuple[str, ...] = (MERGE,)

#: What a workflow's file name stands in for where a reader takes one and means main's push runs,
#: :data:`MAIN_PUSH` in turn (``shards plan --leg test-shard:main-push:junit-test-shard-:3``).
MAIN_PUSH_NAME = "main-push"


def named(name: str) -> tuple[str, ...]:
    """The workflow files *name* stands for, in the order a reader tries them: :data:`MAIN_PUSH`
    for :data:`MAIN_PUSH_NAME`, else the one file."""
    return MAIN_PUSH if name == MAIN_PUSH_NAME else (name,)
