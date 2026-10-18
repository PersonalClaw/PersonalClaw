"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_workflows_judge_contract.py``

Its checks:
``tests/rails/test_workflows_judge_contract_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""


def _repo_src():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "src"
