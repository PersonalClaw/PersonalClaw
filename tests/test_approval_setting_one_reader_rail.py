"""The owner's Approval mode ships asking, and is read as an unattended agent's grant in ONE place.

Settings → Agent defaults → Approval mode (``agent.approval_mode``) shipped as ``auto``, the
loosest end of its own scale, and ``auto`` approves every tool call of an agent no chat started.
So by default every such agent approved its own calls, whatever its trigger's Allow had said. The
mode now ships as ``interactive``, and an unattended run gets its approval from the consent given
for that run (the step's own ``approval_mode``, its trigger's Allow, its loop's Mode, a workflow
run's own unattended grant), never from this setting, unless the owner chooses "auto".

This rail keeps it that way, by the shape of the code rather than by a list of call sites:

* the setting's value is read only in ``approval_grants`` (``approval_mode_now``);
* only ``approval_grants.setting_grant`` reads it as a grant, and only it names the grant;
* ``approval_mode_now`` is called only where it is not a grant: a chat's own trust-reads floor
  and what the Doctor shows;
* the shipped value is the strictest the control has.

A new reader is not wrong by being new: it reds here so the question "is this a grant for a run
nobody consented to?" is answered where it is added.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from personalclaw import approval_grants
from personalclaw.config.editable import _EDITABLE_CONFIG
from personalclaw.config.loader import AgentConfig

SRC = Path(__file__).resolve().parent.parent / "src" / "personalclaw"
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


def _modules() -> list[tuple[str, ast.AST]]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)))
    return out


MODULES = _modules()


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
    assert len(MODULES) > 500, len(MODULES)
    grants = dict(MODULES)[GRANTS]
    assert any(_reads_the_agent_field(n) for n in ast.walk(grants)), "the one reader is gone"


def test_the_setting_is_read_only_in_approval_grants():
    found = [
        f"{rel}:{node.lineno}"
        for rel, tree in MODULES
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
        for rel, tree in MODULES
        if rel != GRANTS
        for line in _names_from_grants(tree, "SETTING")
    ]
    assert found == [], "approval_grants.SETTING named outside setting_grant: " + str(found)
    grants = dict(MODULES)[GRANTS]
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
        for rel, tree in MODULES
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


def test_the_shipped_mode_is_the_strictest_the_control_has():
    control = _EDITABLE_CONFIG["agent.approval_mode"]["security"]
    shipped = AgentConfig().approval_mode
    assert shipped == "interactive"
    assert all(
        not control.loosens(other, shipped)
        for other in _EDITABLE_CONFIG["agent.approval_mode"]["values"]
    ), "some value is stricter than the shipped one"


@pytest.mark.parametrize(("mode", "grant"), [("auto", "setting"), ("interactive", ""), ("", "")])
def test_setting_grant_reads_the_mode_now(monkeypatch, mode, grant):
    monkeypatch.setattr(approval_grants, "approval_mode_now", lambda: mode)
    assert approval_grants.setting_grant() == grant
