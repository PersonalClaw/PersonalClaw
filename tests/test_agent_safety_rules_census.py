"""Every agent core starts is handed the platform's safety rules, and none is started around them.

The rules (the ``safety-rules`` snippet, worded once) reach an agent's model in one of three ways,
and this census reads every place core starts an agent, failing for one it cannot account for:

* **Assembled** — the turn's first message is built by ``ContextBuilder.build_message``, directly or
  through the context engine, and that assembly layers the rules on whatever prompt resolved
  (``prompt_providers.runtime.with_safety_rules``).
* **Layered** — the path composes its own message, and the function that composes it layers them.
* **Calls nothing** — every call the turn asks for is refused, or its runtime has no tools, so
  nothing it is handed can make it act, and its own prompt frames what it answers.

Where an agent is started: each session core acquires (``….get_or_create(…)``, as the session
census reads them), each runtime it builds from the provider factory itself
(``….create_provider_factory()``), and each direct resolution through the bridge
(``resolve_provider_for_use_case``). A new one fails here until it is named with its way, and each
way is checked against the code: an assembled site calls the assembler, a layered one the layer,
and one that calls nothing refuses its calls. Every detector is shown to find what it looks for
before its answer is trusted. What the paths' models are actually handed is read in
``test_every_agent_is_handed_the_safety_rules.py``.

The same sites are read for the agent's own instructions, which the rules follow. A site that NAMES
the agent it starts (an ``agent=`` its session is acquired or its runtime built with) hands that
agent's instructions to its model, read by their one reader
(``agents.instructions.agent_instructions``): its assembly is told the agent, or handed its
instructions read, or the message it composes itself reads them, or it is named in
:data:`NOT_ITS_OWN_WORK` with why its turn is not that agent's own work. A site that names no
agent starts the default one, whose instructions the assembly reads itself. What each path's model
is handed is read in ``test_a_named_agents_instructions_reach_its_model.py``.

What it cannot see: an agent started by a name other than these three.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

from test_session_acquisition_census import REACQUIRES, _census, _functions

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
SNIPPET = SRC / "config" / "prompt_snippets" / "safety-rules.md"

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
#: a room member's session, and the terminal chat's runtime (built from the factory, below).
LAYERED: dict[tuple[str, str], tuple[str, str]] = {
    ("rooms/turn.py", "member_session"): ("rooms/turn.py", "build_member_prompt"),
}
FACTORY_LAYERED: dict[tuple[str, str], tuple[str, str]] = {
    ("cli_chat.py", "_chat"): ("cli_chat.py", "_with_safety_rules"),
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

#: What refusing every call is spelled as.
_REFUSES = frozenset({"REJECT_ALL", "reject_tool"})

#: Runtimes built from the factory that are not agents a person or a schedule starts, with why.
NOT_STARTED_HERE: dict[tuple[str, str], str] = {
    ("gateway.py", "GatewayOrchestrator._init_services"): (
        "the session manager's factory: its sessions are the acquisitions above"
    ),
    ("session.py", "SessionManager.reload_provider_factory"): "the same factory, rebuilt",
    ("cli_commands.py", "_run_eval"): (
        "an evaluation: each scenario's own words, in its own workspace and memory, its calls "
        "answered by the evaluation's allowlist"
    ),
    ("evals/child.py", "_run"): "an evaluation cell, in a throwaway home",
}

#: Direct resolutions through the bridge outside it, with why none starts an agent.
_A_JUDGE = "a judge on the reasoning model, which the bridge builds with no tools"
_SAMPLING_JUDGE = ("sampling.py", "_judge_candidates.provider_factory")
RESOLVED_DIRECTLY: dict[tuple[str, str], str] = {
    ("dashboard/handlers/model_check.py", "api_onboarding_model_check"): (
        "Settings' check that a chat model resolves: built and shut down, sent nothing"
    ),
    _SAMPLING_JUDGE: _A_JUDGE,
    ("learning/replay.py", "replay_proposal.judge_factory.<lambda>"): _A_JUDGE,
}

#: Sites that name the agent they start and are not that agent's own work, by (file, function),
#: with what frames the turn instead of the agent's own instructions.
NOT_ITS_OWN_WORK: dict[tuple[str, str], str] = {
    ("dashboard/side.py", "_run_side_turn"): (
        "a side question about a chat, on its agent's runtime, framed by the side chat's prompt"
    ),
    ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"): (
        "a prompt rewrite on the lite agent, framed by the Prompt optimizer prompt"
    ),
    ("dashboard/handlers/agent_marketplace.py", "api_agent_marketplace_test"): (
        "a marketplace definition's one-turn test, handed the definition's own instructions and "
        "voice: a definition is not one of your agents until it is activated"
    ),
}

#: What tells an assembly the turn's agent: the agent, or its instructions already read.
_TELLS_THE_AGENT = frozenset({"agent", "system_prompt_override"})
#: What assembles a turn's first message.
_ASSEMBLERS = frozenset({"build_message", "assemble_context"})


def _tree(rel: str) -> ast.Module:
    return _package()[rel]


def _names(node: ast.AST) -> set[str]:
    """Every name *node* reads or calls, bare or as an attribute (``x.build_message``)."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            found.add(sub.attr)
    return found


def _uses(rel: str, qualname: str, names: frozenset[str] | set[str]) -> bool:
    func = _functions(_tree(rel)).get(qualname)
    assert func is not None, f"{rel}: {qualname} is gone; update this census"
    return bool(_names(func) & set(names))


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


@functools.cache
def _package() -> dict[str, ast.Module]:
    """Every module of the package, parsed once for the whole census."""
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SRC.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _sites(attr: str, *, skip: frozenset[str] = frozenset()) -> set[tuple[str, str]]:
    return {
        (rel, q) for rel, tree in _package().items() if rel not in skip for q in _calls(tree, attr)
    }


# ── the census ───────────────────────────────────────────────────────────────────────────────


def test_every_session_core_acquires_is_handed_the_rules_or_calls_nothing():
    sites = set(_census())
    assert ("dashboard/chat_runner.py", "run_chat") in sites, "the detector reads the real tree"
    named = set(ASSEMBLED) | set(LAYERED) | set(CALLS_NOTHING) | set(REACQUIRES)
    unaccounted = sorted(sites - named)
    assert not unaccounted, (
        f"a session is acquired at {unaccounted} and this census does not say how its agent is "
        "handed the safety rules: assemble its message with ContextBuilder.build_message, layer "
        "them with prompt_providers.runtime.with_safety_rules, or refuse every call it makes; "
        "then name it here"
    )
    stale = sorted(named - set(REACQUIRES) - sites)
    assert not stale, f"{stale} acquire no session any more; remove them here"


def test_every_runtime_built_from_the_factory_is_accounted_for():
    sites = _sites("create_provider_factory", skip=frozenset({"config/loader.py"}))
    assert ("cli_chat.py", "_chat") in sites, "the detector reads the real tree"
    named = set(NOT_STARTED_HERE) | set(FACTORY_LAYERED)
    assert sites == named, (
        f"unaccounted {sorted(sites - named)}, no longer there {sorted(named - sites)}: a runtime "
        "built from the provider factory is an agent with tools; hand it the safety rules and name "
        "it in FACTORY_LAYERED, or say here why no agent starts there"
    )


def test_every_direct_resolution_through_the_bridge_is_accounted_for():
    sites = _sites(
        "resolve_provider_for_use_case", skip=frozenset({"providers/provider_bridge.py"})
    )
    assert _SAMPLING_JUDGE in sites, "the detector reads the real tree"
    assert sites == set(RESOLVED_DIRECTLY), (
        f"unaccounted {sorted(sites - set(RESOLVED_DIRECTLY))}, no longer there "
        f"{sorted(set(RESOLVED_DIRECTLY) - sites)}: the chat use case builds an agent with tools"
    )


# ── each way, checked against the code ───────────────────────────────────────────────────────


def test_the_assembly_layers_the_rules():
    assert _uses("context.py", "ContextBuilder.build_message", {"with_safety_rules"})


def test_each_assembled_site_calls_the_assembler():
    for rel, qualname in ASSEMBLED:
        assert _uses(rel, qualname, _ASSEMBLERS), f"{rel}: {qualname} no longer assembles"


def test_each_layered_site_layers_the_rules():
    for (rel, qualname), (composer_rel, composer) in {**LAYERED, **FACTORY_LAYERED}.items():
        assert _functions(_tree(rel)).get(qualname) is not None, f"{rel}: {qualname} is gone"
        assert _uses(
            composer_rel, composer, {"with_safety_rules"}
        ), f"{composer_rel}: {composer} composes {qualname}'s message without the safety rules"


def test_each_site_that_calls_nothing_refuses_its_calls():
    for rel, qualname in CALLS_NOTHING:
        assert _uses(rel, qualname, _REFUSES), f"{rel}: {qualname} no longer refuses every call"


def test_the_detectors_find_what_they_look_for():
    """Positive controls: each check answers yes for a function that has the thing, and no for
    one that lacks it, so a zero above is the tree's and not a broken reader's."""
    tree = ast.parse(
        "def a(c):\n    return c.build_message('x', True)\n"
        "def b(p):\n    return p.stream('x')\n"
        "def d(r):\n    return r(lambda k: f(k))\n"
    )
    funcs = _functions(tree)
    assert "build_message" in _names(funcs["a"]) and "build_message" not in _names(funcs["b"])
    assert _calls(tree, "f") == ["d.<lambda>"]
    assert _calls(tree, "build_message") == ["a"]


# ── the agent's own instructions ─────────────────────────────────────────────────────────────


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


def _site(rel: str, qualname: str) -> ast.AST:
    """The function a site names; a lambda's is the function around it, whose own calls hold the
    lambda's."""
    func = _functions(_tree(rel)).get(qualname.removesuffix(".<lambda>"))
    assert func is not None, f"{rel}: {qualname} is gone; update this census"
    return func


def test_the_assembly_reads_the_agents_own_instructions():
    assert _uses("context.py", "ContextBuilder.build_message", {"agent_instructions"})


def test_every_site_that_names_its_agent_hands_it_its_own_instructions():
    sites = (
        set(_census())
        | _sites("create_provider_factory", skip=frozenset({"config/loader.py"}))
        | _sites("resolve_provider_for_use_case", skip=frozenset({"providers/provider_bridge.py"}))
    ) - REACQUIRES
    named = {site for site in sites if _starts_a_named_agent(_site(*site))}
    spawn = ("subagent.py", "SubagentManager._run_inner")
    assert spawn in named, "the detector reads the real tree"
    heartbeat = ("gateway.py", "GatewayOrchestrator._run_heartbeat_task")
    assert heartbeat in sites and heartbeat not in named, "the detector tells a site naming none"
    composers = {**LAYERED, **FACTORY_LAYERED}
    failures: list[str] = []
    for rel, qualname in sorted(named - set(NOT_ITS_OWN_WORK)):
        if (rel, qualname) in ASSEMBLED:
            if not _tells_the_assembly(_site(rel, qualname)):
                failures.append(f"{rel}: {qualname} assembles its message without its agent")
        elif (rel, qualname) in composers:
            composer_rel, composer = composers[(rel, qualname)]
            if not _uses(composer_rel, composer, {"agent_instructions"}):
                failures.append(f"{composer_rel}: {composer} composes without the instructions")
        else:
            failures.append(f"{rel}: {qualname} hands its agent none of its instructions")
    assert not failures, (
        f"{failures}: a site that names the agent it starts hands that agent's own instructions to "
        "its model: tell its assembly the agent (agent=…), compose its message with "
        "agents.instructions.agent_instructions, or name it in NOT_ITS_OWN_WORK with why its turn "
        "is not that agent's own work"
    )
    stale = sorted(set(NOT_ITS_OWN_WORK) - named)
    assert not stale, f"{stale} name no agent any more; remove them from NOT_ITS_OWN_WORK"


def test_the_instruction_detectors_find_what_they_look_for():
    """Positive controls: a site that names its agent and tells its assembly, one that names it
    and does not, one that names none, and a runtime built where the factory is made."""
    funcs = _functions(
        ast.parse(
            "async def told(s, c, a):\n"
            "    p, n, r = await s.get_or_create('k', agent=a)\n"
            "    return await asyncio.to_thread(partial(c.build_message, agent=a), 'x', n)\n"
            "async def untold(s, c, a):\n"
            "    p, n, r = await s.get_or_create('k', agent=a)\n"
            "    return await asyncio.to_thread(c.build_message, 'x', n, 'k')\n"
            "async def nameless(s, c):\n"
            "    p, n, r = await s.get_or_create('k', agent=None)\n"
            "    def later():\n"
            "        return c.build_message('x', n, agent='other')\n"
            "    return later\n"
            "def factory(cfg, a):\n"
            "    return cfg.create_provider_factory()('k', agent=a)\n"
        )
    )
    assert _starts_a_named_agent(funcs["told"]) and _starts_a_named_agent(funcs["untold"])
    assert not _starts_a_named_agent(funcs["nameless"]) and _starts_a_named_agent(funcs["factory"])
    assert _tells_the_assembly(funcs["told"]) and not _tells_the_assembly(funcs["untold"])
    assert not _tells_the_assembly(funcs["nameless"]), "a nested function is its own site"


# ── one wording ──────────────────────────────────────────────────────────────────────────────


def _rules() -> list[str]:
    lines = SNIPPET.read_text(encoding="utf-8").splitlines()
    return [r for r in (line.lstrip("- ").strip() for line in lines) if r]


def _copied(text: str) -> list[str]:
    """The snippet's rules *text* states word for word."""
    return [r for r in _rules() if r in text]


def test_the_rules_are_worded_in_one_place():
    """The snippet is the one source: no other file in the package states one of its rules, so
    there is no copy to drift from the words the owner edits in Settings → Prompts."""
    assert len(_rules()) >= 4, "the snippet holds the rules"
    assert _copied(f"Rules:\n- {_rules()[-1]}\n") == [_rules()[-1]], "a copy is found"
    copies = [
        (path.relative_to(SRC).as_posix(), rule[:60])
        for path in sorted(SRC.rglob("*"))
        if path.is_file()
        and path != SNIPPET
        and path.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".txt"}
        for rule in _copied(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not copies, f"a rule is copied out of the snippet: {copies}"
