"""The workflows a candidate's checks run in, named in one place (#3915).

Until the flip, a pull request's checks and a push to main's run in ``ci.yml`` (:data:`LEGACY`).
``pr.yml`` (:data:`FEEDBACK`, the change tier) and ``merge.yml`` (:data:`MERGE`, the merge tier and
the push stage) run beside it on dispatch; the flip gives them its triggers and deletes it. Each
runtime reader of a candidate's runs names the workflows through this module and reads both shapes
while both exist, ``ci.yml``'s one run or the pair, so the flip strands none of them.
``tests/rails/test_the_candidate_workflows_are_named_in_one_place_tree.py`` fails any other file
that spells ``ci.yml`` in code, unless its baseline lists the file with the reason it still does;
the baseline only shrinks, and the flip lands with it empty.

Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

#: The one workflow of today's checks, which the flip deletes.
LEGACY = "ci.yml"
#: The change tier's quick jobs, on every pull request once the flip lands.
FEEDBACK = "pr.yml"
#: The merge tier on every pull request, and the push stage on every push to main.
MERGE = "merge.yml"

#: Each workflow by its ``name:``, the name a ``workflow_run`` listener follows it by.
NAMES: dict[str, str] = {LEGACY: "CI", FEEDBACK: "PR", MERGE: "Merge"}

#: The workflows whose push to main is that commit's run, the newer shape first: merge.yml once
#: the flip lands, ci.yml until then.
MAIN_PUSH: tuple[str, ...] = (MERGE, LEGACY)

#: What a workflow's file name stands in for where a reader takes one and means main's push runs
#: in either shape, :data:`MAIN_PUSH` in turn, so a workflow that passes it spells neither file
#: (``shards plan --leg test-shard:main-push:junit-test-shard-:3``).
MAIN_PUSH_NAME = "main-push"


def named(name: str) -> tuple[str, ...]:
    """The workflow files *name* stands for, in the order a reader tries them: :data:`MAIN_PUSH`
    for :data:`MAIN_PUSH_NAME`, else the one file."""
    return MAIN_PUSH if name == MAIN_PUSH_NAME else (name,)
