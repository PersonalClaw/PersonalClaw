"""The source-tree checks split from
``tests/test_workflows_surfacing_channels.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from personalclaw.workflows.surfacing_channels import propose_packs


def test_a_proposal_is_ONE_grouped_suggestion():
    """Five separate "enable this SOP?" prompts on attach is a wall the user clicks away — and
    clicking away a wall teaches them to click away the next one."""
    proposal = propose_packs(["pyproject.toml", "tests", ".github/workflows", "Makefile"])
    assert proposal is not None
    assert len(proposal.packs) >= 2
    assert isinstance(proposal.defs, list)
