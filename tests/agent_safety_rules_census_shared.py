"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_agent_safety_rules_census.py``

Its checks:
``tests/rails/test_agent_safety_rules_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: Sites whose first message ``build_message`` assembles, by (file, function), with what runs there.
ASSEMBLED: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat"): (
        "a chat's turns: the default agent, a routed agent, a goal loop's and a Code project's "
        "workers and planners, an app's conversation"
    ),
    ("subagent.py", "SubagentManager._run_inner"): (
        "a spawned agent: a chat's subagent, a workflow stage, an automation's agent, an app's "
        "agent run (a text run is handed its task alone, on a runtime with no tools)"
    ),
    ("gateway.py", "GatewayOrchestrator._init_subagents._subagent_done"): (
        "a finished subagent's result, read into the conversation that started it"
    ),
    ("gateway.py", "GatewayOrchestrator._run_heartbeat_task"): "a heartbeat task",
    ("dashboard/handlers/hooks.py", "_run_hook_inner"): "a webhook's turn",
}


#: Sites that compose their own message, by (file, function), with the function that composes it:
#: a room member's session.
LAYERED: dict[tuple[str, str], tuple[str, str]] = {
    ("rooms/turn.py", "member_session"): ("rooms/turn.py", "build_member_prompt"),
}


#: Sites whose turns can run nothing, by (file, function), with why.
CALLS_NOTHING: dict[tuple[str, str], str] = {
    ("dashboard/side.py", "_run_side_turn"): "a side question about a chat; every call refused",
    ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"): (
        "a prompt rewrite on the lite agent, built with no tools; a call is refused"
    ),
    ("dashboard/handlers/agent_marketplace.py", "api_agent_marketplace_test"): (
        "a marketplace agent's one-turn test; every call refused"
    ),
}


#: What tells an assembly the turn's agent: the agent, or its instructions already read.
_TELLS_THE_AGENT = frozenset({"agent", "system_prompt_override"})


#: What assembles a turn's first message.
_ASSEMBLERS = frozenset({"build_message", "assemble_context"})


def _names(node: ast.AST) -> set[str]:
    """Every name *node* reads or calls, bare or as an attribute (``x.build_message``)."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def _called(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _calls(tree: ast.Module, attr: str) -> list[str]:
    """The qualified name of the function around each call to ``attr`` (as a name or attribute),
    once per call, a lambda's included (``outer.<lambda>``)."""
    out: list[str] = []

    def visit(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*scope, child.name])
            elif isinstance(child, ast.Lambda):
                visit(child, [*scope, "<lambda>"])
            else:
                if isinstance(child, ast.Call) and _called(child) == attr:
                    out.append(".".join(scope))
                visit(child, scope)

    visit(tree, [])
    return out


def _own_calls(func: ast.AST) -> list[ast.Call]:
    """The calls in *func*'s own body, a nested function's left out, as the acquisition census
    reads a site."""
    nested = {
        id(node)
        for inner in ast.walk(func)
        if inner is not func and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(inner)
    }
    return [
        node for node in ast.walk(func) if isinstance(node, ast.Call) and id(node) not in nested
    ]


def _starts(call: ast.Call) -> bool:
    """A call that starts an agent: an acquisition, a direct resolution through the bridge, or a
    runtime built where the factory is made (``….create_provider_factory()(…)``)."""
    if _called(call) in {"get_or_create", "resolve_provider_for_use_case"}:
        return True
    return isinstance(call.func, ast.Call) and _called(call.func) == "create_provider_factory"


def _names_an_agent(call: ast.Call) -> bool:
    """Whether *call* names the agent it starts: an ``agent=`` other than a literal ``None``."""
    return any(
        kw.arg == "agent" and not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
        for kw in call.keywords
    )


def _starts_a_named_agent(func: ast.AST) -> bool:
    return any(_starts(call) and _names_an_agent(call) for call in _own_calls(func))


def _tells_the_assembly(func: ast.AST) -> bool:
    """Whether a call that names an assembler (calling it, or handing it to a worker thread or a
    ``partial``) tells it the turn's agent."""
    return any(
        _names(call) & _ASSEMBLERS and {kw.arg for kw in call.keywords} & _TELLS_THE_AGENT
        for call in _own_calls(func)
    )
