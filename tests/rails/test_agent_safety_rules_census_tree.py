"""The source-tree checks split from
``tests/test_agent_safety_rules_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from agent_safety_rules_census_shared import (
    _ASSEMBLERS,
    ASSEMBLED,
    CALLS_NOTHING,
    LAYERED,
    _calls,
    _names,
    _starts_a_named_agent,
    _tells_the_assembly,
)
from rails_repo import REPO
from session_acquisitions import REACQUIRES, _census, _functions

SRC = REPO / "src" / "personalclaw"


SNIPPET = SRC / "config" / "prompt_snippets" / "safety-rules.md"


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


#: Direct resolutions through the bridge outside it, with why none starts an agent. A call that
#: only wants a model's answer (a judge, a chore) resolves it as the model itself
#: (``resolve_metered_model``), which never builds an agent, so it is not one of these.
_MODEL_CHECK = ("dashboard/handlers/model_check.py", "api_onboarding_model_check")


RESOLVED_DIRECTLY: dict[tuple[str, str], str] = {
    _MODEL_CHECK: "Settings' check that a chat model resolves: built and shut down, sent nothing",
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


def _tree(rel: str) -> ast.Module:
    return _package()[rel]


def _uses(rel: str, qualname: str, names: frozenset[str] | set[str]) -> bool:
    func = _functions(_tree(rel)).get(qualname)
    assert func is not None, f"{rel}: {qualname} is gone; update this census"
    return bool(_names(func) & set(names))


def _package() -> dict[str, ast.Module]:
    """Every module of the package, parsed, from the suite's one source index."""
    return {path.relative_to(SRC).as_posix(): tree for path, tree in source_index.package_trees()}


def _sites(attr: str, *, skip: frozenset[str] = frozenset()) -> set[tuple[str, str]]:
    return {
        (rel, q) for rel, tree in _package().items() if rel not in skip for q in _calls(tree, attr)
    }


def test_every_runtime_built_from_the_factory_is_accounted_for():
    """An agent a person or a schedule starts is never built from the provider factory directly.
    The terminal chat was the last one, and it is a chat of the gateway's now, which ``run_chat``
    assembles like every chat."""
    sites = _sites("create_provider_factory", skip=frozenset({"config/loader.py"}))
    assert ("gateway.py", "GatewayOrchestrator._init_services") in sites, "the detector reads"
    named = set(NOT_STARTED_HERE)
    assert sites == named, (
        f"unaccounted {sorted(sites - named)}, no longer there {sorted(named - sites)}: a runtime "
        "built from the provider factory is an agent with tools; start it as a session the turn "
        "engine assembles (the census above reads those), or say here why no agent starts there"
    )


def test_every_direct_resolution_through_the_bridge_is_accounted_for():
    sites = _sites(
        "resolve_provider_for_use_case", skip=frozenset({"providers/provider_bridge.py"})
    )
    assert _MODEL_CHECK in sites, "the detector reads the real tree"
    assert sites == set(RESOLVED_DIRECTLY), (
        f"unaccounted {sorted(sites - set(RESOLVED_DIRECTLY))}, no longer there "
        f"{sorted(set(RESOLVED_DIRECTLY) - sites)}: the chat use case builds an agent with tools"
    )


def test_the_assembly_layers_the_rules():
    assert _uses("context.py", "ContextBuilder.build_message", {"with_safety_rules"})


def test_each_assembled_site_calls_the_assembler():
    for rel, qualname in ASSEMBLED:
        assert _uses(rel, qualname, _ASSEMBLERS), f"{rel}: {qualname} no longer assembles"


def test_each_layered_site_layers_the_rules():
    for (rel, qualname), (composer_rel, composer) in LAYERED.items():
        assert _functions(_tree(rel)).get(qualname) is not None, f"{rel}: {qualname} is gone"
        assert _uses(
            composer_rel, composer, {"with_safety_rules"}
        ), f"{composer_rel}: {composer} composes {qualname}'s message without the safety rules"


def test_each_site_that_calls_nothing_refuses_its_calls():
    for rel, qualname in CALLS_NOTHING:
        assert _uses(rel, qualname, _REFUSES), f"{rel}: {qualname} no longer refuses every call"


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
    composers = LAYERED
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
        for path in source_index.package_any_files()
        if path != SNIPPET and path.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".txt"}
        for rule in _copied(source_index.text(path))
    ]
    assert not copies, f"a rule is copied out of the snippet: {copies}"


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
