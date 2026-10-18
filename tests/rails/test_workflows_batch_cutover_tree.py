"""The source-tree checks split from
``tests/test_workflows_batch_cutover.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from personalclaw.workflows import roster


def test_the_roster_projects_over_the_SAME_source_validate_agent_consults():
    """One source for "which agents exist". `roster.catalog` reads `AppConfig.load().agents` and
    `subagent._validate_agent` checks `AppConfig.load().agents` — the same dict. Asserted by AST so
    a future edit that introduced a second enumeration trips here rather than drifting silently."""
    import ast
    import inspect

    from personalclaw import subagent as subagent_mod

    def _reads_appconfig_agents(fn) -> bool:
        tree = ast.parse(inspect.getsource(fn).lstrip())
        return any(
            isinstance(n, ast.Attribute) and n.attr == "agents" for n in ast.walk(tree)
        ) and "AppConfig" in inspect.getsource(fn)

    assert _reads_appconfig_agents(roster.catalog)
    assert _reads_appconfig_agents(subagent_mod._validate_agent)
