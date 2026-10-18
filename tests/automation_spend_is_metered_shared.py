"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_automation_spend_is_metered.py``

Its checks:
``tests/rails/test_automation_spend_is_metered_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Surfaces a person is working in. The daily cap is for automations; these stay on the chat
#: binding, as a chat does. Keyed by (file under ``src/personalclaw``, function qualname).
INTERACTIVE: dict[tuple[str, str], str] = {
    ("dashboard/handlers/agent_marketplace.py", "api_agent_marketplace_test"): (
        "an agent's test run from its page: a person pressed the button and reads the reply"
    ),
    ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"): (
        "the composer's prompt rewrite: a person pressed the button and waits for the text"
    ),
    ("rooms/turn.py", "member_session"): (
        "a room member answering in a room a person reads; `routing/usage.py` counts a room's "
        "turns as interactive spend"
    ),
}


#: Calls whose axis a named function decides, keyed like INTERACTIVE: the call passes
#: ``model_axis=<function>(...)``, and the tests below pin what the function answers. A chat's
#: turn is on the chat binding, or on Code & tools when it works in a folder of its own, both of
#: which the cap leaves alone as the Guardrails page says, unless it is a loop's worker or planner.
#: A side question a person asks beside their chat is answered on the chat's own axis, while they
#: wait. A finished subagent's announcement keeps the chat binding in a channel thread, a person's
#: conversation, and is metered in any other parent.
DECIDED: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat"): "model_axis_for",
    ("dashboard/side.py", "_run_side_turn"): "chat_model_axis",
    ("gateway.py", "GatewayOrchestrator._init_subagents._subagent_done"): "announce_axis",
}


#: The manager's own re-acquisitions: they replay a request someone already made, whole.
REACQUIRES: dict[tuple[str, str], str] = {
    ("session.py", "SessionManager.get_or_create"): (
        "rebuilds a stale runtime with the request its caller has just made, axis included"
    ),
}


#: What hands a caller a model to call directly, with no runtime acquired.
RESOLVERS = frozenset(
    {"resolve_provider_for_use_case", "resolve_metered_model", "resolve_image_reader"}
)


#: Direct resolutions that stay unmetered, keyed like INTERACTIVE, with the reason.
UNMETERED_RESOLUTIONS: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "_describe_screen_frame"): (
        "the reading of the screen a person shared into their own chat turn, for the turn they "
        "are waiting on, and unmetered like it"
    ),
    ("dashboard/handlers/model_check.py", "api_onboarding_model_check"): (
        "onboarding's check that a chat model builds, which a person pressed: it builds the model "
        "and calls nothing"
    ),
}


def guarded_axes() -> frozenset[str]:
    """The axes the guard wraps, as ``provider_bridge`` decides them — and the one line there that
    asks, so a constant nothing reads cannot stand in for the rule."""
    from personalclaw.providers import provider_bridge

    text = Path(provider_bridge.__file__).read_text(encoding="utf-8")
    assert re.search(
        r'if _metered or use_case in METERED_AXES:\n\s+kwargs\["_guard_use_case"\]', text
    ), "the guard's axis test moved: this rail reads it to know which axes are metered"
    axes = frozenset(provider_bridge.METERED_AXES)
    assert axes and "chat" not in axes, axes
    return axes


@dataclass(frozen=True)
class Site:
    path: str
    qualname: str
    line: int
    call: ast.Call
    func: ast.AST | None


class _Census(ast.NodeVisitor):
    """Every call *counted* answers for, with the function it sits in."""

    def __init__(self, path: str, counted: Callable[[ast.Call], bool]) -> None:
        self.path = path
        self.counted = counted
        self.stack: list[str] = []
        self.funcs: list[ast.AST] = []
        self.sites: list[Site] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.funcs.append(node)
        self.generic_visit(node)
        self.funcs.pop()
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        if self.counted(node):
            func = self.funcs[-1] if self.funcs else None
            self.sites.append(Site(self.path, ".".join(self.stack), node.lineno, node, func))
        self.generic_visit(node)


def _acquires(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "get_or_create"


def _resolves(call: ast.Call) -> bool:
    return _called(call) in RESOLVERS


def census(tree: ast.AST, path: str, counted: Callable[[ast.Call], bool] = _acquires) -> list[Site]:
    visitor = _Census(path, counted)
    visitor.visit(tree)
    return visitor.sites


def _called(expr: ast.AST | None) -> str:
    """The bare name *expr* calls, for ``f(...)`` and ``mod.f(...)``; ``""`` for anything else."""
    if not isinstance(expr, ast.Call):
        return ""
    func = expr.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def verdict(site: Site, guarded: frozenset[str]) -> str:
    """``guarded`` | ``decided`` | ``reacquires`` | ``interactive`` | ``""`` (unaccounted)."""
    keywords = {k.arg: k.value for k in site.call.keywords if k.arg}
    axis = keywords.get("model_axis")
    if isinstance(axis, ast.Constant) and axis.value in guarded:
        return "guarded"
    decider = DECIDED.get((site.path, site.qualname))
    if decider and _called(axis) == decider:
        return "decided"
    where = (site.path, site.qualname)
    if where in REACQUIRES and any(k.arg is None for k in site.call.keywords):
        return "reacquires"
    if where in INTERACTIVE:
        return "interactive"
    return ""


def resolution_verdict(site: Site, guarded: frozenset[str]) -> str:
    """``guarded`` | ``metered`` | ``listed`` | ``""`` (unaccounted), for a direct resolution."""
    keywords = {k.arg: k.value for k in site.call.keywords if k.arg}
    resolver = _called(site.call)
    if resolver == "resolve_metered_model":
        return "metered"
    if resolver == "resolve_image_reader":
        asked = keywords.get("metered")
        if isinstance(asked, ast.Constant) and asked.value is True:
            return "metered"
    if resolver == "resolve_provider_for_use_case":
        axis = site.call.args[0] if site.call.args else keywords.get("use_case")
        if isinstance(axis, ast.Constant) and axis.value in guarded:
            return "guarded"
    if (site.path, site.qualname) in UNMETERED_RESOLUTIONS:
        return "listed"
    return ""
