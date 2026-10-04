"""Every place PersonalClaw approves a call asks the deny-list first, through the one screen.

The deny-list is a floor whatever the approval mode, and it is asked on the command the call would
run: ``acp.permission_authority.screen_tool_call``. The background helper asked it under one mode
only, the evaluation runner not at all, and the chat's runner read the command itself, beside the
screen, so the two readings could drift. This rail classifies every call that answers an approval
(``approve_tool``) in ``src/personalclaw`` (``file::qualname``) as one of two:

* ``_DECIDES`` — the site decides the call: its module asks the screen (``screen_tool_call``, or
  ``run_bounds.screen``, which asks it and reads the allowed hosts beside it);
* ``_FORWARDS`` — a runtime's own ``approve_tool``, handing on an answer its caller decided.

A call can also run with no approval asked at all: a native runtime's own gate answers every
call its approval policy waives (a chat's Trust or YOLO, a subagent's standing grant), and Tools →
Try it runs a tool directly. Each gate in ``_GATES`` asks the screen too, in its own body or
through the one function named beside it, which it calls.

And it keeps the reading in one place: ``command_probe`` is called only by the screen, a hook
manager's verdict on a call (``.on_tool_call``) is asked only there too, and so are the tool-name
deny patterns (``security.is_denied``), which the hook chain reads with the operator's own. A new
site reds this rail by name until it is classified, and a stale entry reds it too. The
falsification test at the bottom runs the checks on source written to fail them. A channel app that
runs a conversation itself asks the same screen through ``personalclaw.sdk.channel`` (the
``tool-call-screen`` core feature).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The calls that are the screen, by callee.
SCREENS = frozenset({"screen_tool_call", "run_bounds.screen"})

_DECIDES: dict[str, str] = {
    "dashboard/chat_runner.py::run_chat": "a chat's call: the card, Trust, YOLO, a hook pattern",
    "llm_helpers.py::_resolve_permission": (
        "the background helper, under every approval mode: an announce, a heartbeat, a room "
        "member's turn, a one-shot call"
    ),
    "subagent.py::SubagentManager._approve_and_log": "a subagent's call, screened in its turn",
    "eval/runner.py::EvalRunner._decide_permission": "an evaluation's allowlist",
}

_FORWARDS: dict[str, str] = {
    "acp/client.py::AcpClient.approve_tool": "the agent CLI client, to its session",
    "guardrails/model_call.py::ModelCallGuard.approve_tool": "the spend guard, to its runtime",
    "llm/acp_agent.py::AcpAgentProvider.approve_tool": "the agent CLI provider, to its client",
    "llm/acp_session_provider.py::AcpSessionProvider.approve_tool": "a pooled session, to it",
}

#: The gates that run a call no approval is asked for, each of which asks the screen: each gate,
#: the function it asks the screen through ("" when it asks in its own body), and what it is.
_GATES: dict[str, tuple[str, str]] = {
    "agents/native/runtime.py::NativeAgentRuntime._guard_and_invoke": (
        "agents/native/approval.py::deny_list_refusal",
        "a native runtime's own gate, which answers every call its approval policy waives",
    ),
    "dashboard/handlers/tools.py::api_tool_invoke": (
        "",
        "Tools → Try it and a scheduled script's tool call, by the tool's name",
    ),
}

#: The one place the command is probed and the hook chain is asked about a call.
THE_SCREEN = "acp/permission_authority.py::screen_tool_call"

#: Where the tool-name deny patterns are read: the hook chain, with the operator's own.
THE_CHAIN = "hooks.py::HookManager.on_tool_call"

#: Calls named ``on_tool_call`` that are not the hook chain's verdict.
_NOT_THE_HOOK_CHAIN = {
    "dashboard/chat_runner.py::run_chat::chat_questions.on_tool_call": (
        "the question tool's own card, not a verdict on the call"
    ),
}


def _callee(node: ast.Call) -> str:
    f = node.func
    parts: list[str] = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _calls(tree: ast.AST, rel: str) -> list[tuple[str, str]]:
    """Every call in *tree* as (``file::qualname``, callee)."""
    found: list[tuple[str, str]] = []

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.q: list[str] = []

        def visit_FunctionDef(self, n: ast.AST) -> None:
            self.q.append(n.name)  # type: ignore[attr-defined]
            self.generic_visit(n)
            self.q.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]
        visit_ClassDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, n: ast.Call) -> None:
            found.append((f"{rel}::{'.'.join(self.q) or '<module>'}", _callee(n)))
            self.generic_visit(n)

    V().visit(tree)
    return found


def _problems(modules: dict[str, ast.AST]) -> dict[str, list[str]]:
    """What each check finds wrong in *modules* (``rel path`` → parsed source)."""
    approvals: set[str] = set()
    screening: set[str] = set()
    screened_sites: set[str] = set()
    probes: set[str] = set()
    chains: set[str] = set()
    name_patterns: set[str] = set()
    called: dict[str, set[str]] = {}
    for rel, tree in modules.items():
        for site, callee in _calls(tree, rel):
            tail = callee.rsplit(".", 1)[-1]
            called.setdefault(site, set()).add(tail)
            if tail == "approve_tool":
                approvals.add(site)
            elif callee in SCREENS or tail == "screen_tool_call":
                screening.add(rel)
                screened_sites.add(site)
            elif tail == "command_probe":
                probes.add(site)
            elif tail == "on_tool_call" and f"{site}::{callee}" not in _NOT_THE_HOOK_CHAIN:
                chains.add(site)
            elif callee in ("is_denied", "security.is_denied"):
                name_patterns.add(site)
    classified = set(_DECIDES) | set(_FORWARDS)
    return {
        "unclassified": sorted(approvals - classified),
        "unscreened": sorted(
            s for s in approvals & set(_DECIDES) if s.split("::")[0] not in screening
        ),
        "not_forwarding": sorted(
            s for s in approvals & set(_FORWARDS) if s.rsplit(".", 1)[-1] != "approve_tool"
        ),
        "probed_elsewhere": sorted(probes - {THE_SCREEN}),
        "chain_asked_elsewhere": sorted(chains - {THE_SCREEN}),
        "gates_unscreened": sorted(
            gate
            for gate, (via, _what) in _GATES.items()
            if gate.split("::")[0] in modules
            and not (
                via in screened_sites
                and via.split("::")[-1].rsplit(".", 1)[-1] in called.get(gate, set())
                if via
                else gate in screened_sites
            )
        ),
        "name_patterns_elsewhere": sorted(name_patterns - {THE_CHAIN}),
        "_approvals": sorted(approvals),
    }


def _tree() -> dict[str, ast.AST]:
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SRC.rglob("*.py"))
    }


def test_every_approval_site_is_classified_and_none_is_stale():
    found = _problems(_tree())
    assert found["unclassified"] == [], (
        "a call is approved here with no entry in this rail: classify it in _DECIDES (and ask "
        "screen_tool_call before it) or in _FORWARDS"
    )
    stale = sorted((set(_DECIDES) | set(_FORWARDS)) - set(found["_approvals"]))
    assert stale == [], f"these entries approve nothing any more: {stale}"


def test_every_site_that_decides_a_call_asks_the_screen():
    assert _problems(_tree())["unscreened"] == []


def test_a_forwarding_site_is_a_runtimes_own_approve_tool():
    assert _problems(_tree())["not_forwarding"] == []


def test_every_gate_that_runs_a_call_unasked_asks_the_screen():
    tree = _tree()
    named = {site for rel, module in tree.items() for site, _callee in _calls(module, rel)}
    through = {via for via, _what in _GATES.values() if via}
    assert set(_GATES) | through <= named, "a gate this rail names no longer exists: update _GATES"
    assert _problems(tree)["gates_unscreened"] == []


def test_the_command_is_read_and_the_hook_chain_asked_in_the_screen_alone():
    found = _problems(_tree())
    assert found["probed_elsewhere"] == [], "probe the command through screen_tool_call"
    assert found["chain_asked_elsewhere"] == [], "ask the hook chain through screen_tool_call"
    assert found["name_patterns_elsewhere"] == [], "ask the deny patterns through screen_tool_call"


def test_the_checks_fail_source_written_to_fail_them():
    decides = next(iter(_DECIDES))
    rel, qualname = decides.split("::")
    *owner, name = qualname.split(".")
    body = (
        f"async def {name}(provider, event):\n    await provider.approve_tool(event.request_id)\n"
    )
    if owner:
        body = f"class {owner[0]}:\n" + "".join(f"    {line}\n" for line in body.splitlines())
    loose = (
        "def gate(hooks, event):\n"
        "    command_probe(event.title, '')\n"
        "    security.is_denied(event.title)\n"
        "    return hooks.on_tool_call(event.title)\n"
        "async def grant(provider, event):\n"
        "    await provider.approve_tool(event.request_id)\n"
    )
    gate, gate_qualname = next(iter(_GATES)).split("::")
    *gate_owner, gate_name = gate_qualname.split(".")
    unscreened_gate = f"def {gate_name}(self, tool, args):\n    return security.is_denied(tool)\n"
    if gate_owner:
        unscreened_gate = f"class {gate_owner[0]}:\n" + "".join(
            f"    {line}\n" for line in unscreened_gate.splitlines()
        )
    found = _problems(
        {rel: ast.parse(body), "elsewhere.py": ast.parse(loose), gate: ast.parse(unscreened_gate)}
    )
    assert found["unscreened"] == [decides]
    assert found["unclassified"] == ["elsewhere.py::grant"]
    assert found["probed_elsewhere"] == ["elsewhere.py::gate"]
    assert found["chain_asked_elsewhere"] == ["elsewhere.py::gate"]
    assert found["gates_unscreened"] == [next(iter(_GATES))]
    assert found["name_patterns_elsewhere"] == sorted(["elsewhere.py::gate", next(iter(_GATES))])

    # A gate that asks through a function: screened only when that function asks the screen.
    through, (via, _what) = next((g, entry) for g, entry in _GATES.items() if entry[0])
    gate, gate_qualname = through.split("::")
    via_rel, via_name = via.split("::")
    *gate_owner, gate_name = gate_qualname.split(".")
    calls_through = f"def {gate_name}(self, tool, args):\n    return {via_name}(tool, args)\n"
    if gate_owner:
        calls_through = f"class {gate_owner[0]}:\n" + "".join(
            f"    {line}\n" for line in calls_through.splitlines()
        )
    asks = f"def {via_name}(tool, args):\n    return screen_tool_call(None, tool, args)\n"
    asks_nothing = f"def {via_name}(tool, args):\n    return None\n"
    for helper, unscreened in ((asks, []), (asks_nothing, [through])):
        found = _problems({gate: ast.parse(calls_through), via_rel: ast.parse(helper)})
        assert found["gates_unscreened"] == unscreened
