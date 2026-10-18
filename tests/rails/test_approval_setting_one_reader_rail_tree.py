"""The source-tree checks split from
``tests/test_approval_setting_one_reader_rail.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


GRANTS = "approval_grants.py"


#: Where ``approval_mode_now`` may be called, each with why it is not a grant for an agent no chat
#: started.
NOT_A_GRANT = {
    # Its own module: `setting_grant` and `setting_sentence` are built on it.
    GRANTS: "the one reader",
    # A chat's floor: "trust_reads" lets an attended chat run a read-only shell command unasked.
    "dashboard/chat_runner.py": "a chat's own trust-reads floor",
    # What the Doctor's row shows as evidence.
    "resilience/doctor.py": "what the Doctor shows",
}


def _modules() -> tuple[tuple[str, ast.AST], ...]:
    """Every module of the package, parsed, from the suite's one source index."""
    return tuple(
        (path.relative_to(SRC).as_posix(), tree) for path, tree in source_index.package_trees()
    )


def _reads_the_agent_field(node: ast.AST) -> bool:
    """``<anything>.agent.approval_mode``, or ``getattr(<anything>.agent, "approval_mode")``."""
    if isinstance(node, ast.Attribute) and node.attr == "approval_mode":
        return isinstance(node.value, ast.Attribute) and node.value.attr == "agent"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
        args = node.args
        return (
            len(args) >= 2
            and isinstance(args[0], ast.Attribute)
            and args[0].attr == "agent"
            and isinstance(args[1], ast.Constant)
            and args[1].value == "approval_mode"
        )
    return False


def _names_from_grants(tree: ast.AST, name: str) -> list[int]:
    """Lines where *name* of `approval_grants` is used: ``approval_grants.<name>``, or imported."""
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == name
            and isinstance(node.value, ast.Name)
            and node.value.id == "approval_grants"
        ):
            lines.append(node.lineno)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("approval_grants"):
            lines.extend(node.lineno for alias in node.names if alias.name == name)
    return lines


def test_the_scan_reads_the_tree():
    """The vacuity floor: the walk sees the package, and the one reader it allows is there."""
    assert len(_modules()) > 500, len(_modules())
    grants = dict(_modules())[GRANTS]
    assert any(_reads_the_agent_field(n) for n in ast.walk(grants)), "the one reader is gone"


def test_the_setting_is_read_only_in_approval_grants():
    found = [
        f"{rel}:{node.lineno}"
        for rel, tree in _modules()
        if rel != GRANTS
        for node in ast.walk(tree)
        if _reads_the_agent_field(node)
    ]
    assert found == [], (
        "agent.approval_mode is read outside approval_grants; read it through "
        "approval_grants.approval_mode_now (a value) or setting_grant (a grant): " + str(found)
    )


def test_only_setting_grant_names_the_grant():
    """`SETTING` is what an audit row says approved a call; only `setting_grant` returns it."""
    found = [
        f"{rel}:{line}"
        for rel, tree in _modules()
        if rel != GRANTS
        for line in _names_from_grants(tree, "SETTING")
    ]
    assert found == [], "approval_grants.SETTING named outside setting_grant: " + str(found)
    grants = dict(_modules())[GRANTS]
    returns = [
        node.name
        for node in ast.walk(grants)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(n, ast.Return) and "SETTING" in ast.unparse(n.value or ast.Constant(""))
            for n in ast.walk(node)
        )
    ]
    assert returns == ["setting_grant"], returns


def test_the_value_is_read_only_where_it_is_not_a_grant():
    found = {
        rel
        for rel, tree in _modules()
        if _names_from_grants(tree, "approval_mode_now")
        or (
            rel == GRANTS
            and any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "approval_mode_now"
                for n in ast.walk(tree)
            )
        )
    }
    assert found == set(NOT_A_GRANT), (
        "approval_mode_now's callers changed; a new one must not decide an unattended agent's "
        f"approval (that is setting_grant): {sorted(found ^ set(NOT_A_GRANT))}"
    )
