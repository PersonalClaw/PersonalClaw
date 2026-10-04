"""An agent's skill list decides which skills its turns are given, and which its skill tools reach.

The Agents page offers an agent a list of skills, and the Skills page says which agents use each
skill, from those lists. Measured before this: nothing that assembles a turn read the list. A custom
agent was offered no skill whatever its list said, the default agent every skill whatever its list
said, and the skill tools (``skill_search``, ``skill_invoke``, ``skill_resource``) reached the whole
library for every agent.

Now the list is read in one place (``agents.skill_list.agent_skills``), where a turn's context is
assembled (``ContextBuilder``) and where PersonalClaw's own runtime dispatches a call, which holds
it for the skill tools. A list is the whole of what the agent's turns are offered and its tools
reach. An agent with no list is offered what it always was: the default agent every skill, another
agent none. An agent on an agent CLI is held to no list, since the CLI loads its own skills. A
loop's own skills sit beside its agent's list on the loop's turns, and the built-in loop worker
carries no list.

The turns here are assembled by the real ``ContextBuilder`` over a real skills library in the test's
own home, and the runtime is built by the production factory
(``provider_bridge._build_native_runtime``) with the real in-process core tools; only the model is a
fake, which calls what each test scripts.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.agents.defaults import make_default_native_profile
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.hooks import HookManager
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

#: The library: two skills offered when a message fits their triggers, and one always-on.
SKILLS = {
    "alpha": (
        "---\nname: alpha\ndescription: Steps for the alpha topic\ntriggers: alpha topic\n"
        "resources:\n  - path: notes.md\n    description: alpha notes\n---\n# Alpha\nALPHA-STEPS\n"
    ),
    "beta": (
        "---\nname: beta\ndescription: Steps for the beta topic\ntriggers: beta topic\n"
        "resources:\n  - path: notes.md\n    description: beta notes\n---\n# Beta\nBETA-STEPS\n"
    ),
    "gamma": (
        "---\nname: gamma\ndescription: A house rule\nalways: true\n---\n# Gamma\nGAMMA-RULE\n"
    ),
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The test's own home, with the library above and the agents the tests name."""
    # The bundled skills would join the library on every loader built: they are not this test's.
    monkeypatch.setattr("personalclaw.skills.loader._ensure_builtin_skills", lambda _base: None)
    root = config_loader.config_dir()
    for name, text in SKILLS.items():
        folder = root / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(text, encoding="utf-8")
        (folder / "notes.md").write_text(f"{name} notes", encoding="utf-8")
    cfg = AppConfig.load()
    cfg.agents.update(
        {
            # The seeded default agent, as the gateway's first start writes it.
            "PersonalClaw": make_default_native_profile(AgentProfile),
            "researcher": AgentProfile(provider="native", skills=["alpha", "gamma"]),
            "plain": AgentProfile(provider="native"),
            "reviewer": AgentProfile(provider="acp:claude-code", skills=["alpha"]),
        }
    )
    cfg.default_agent = "PersonalClaw"
    cfg.save()
    return root


def _set_default_agent_skills(skills: list[str]) -> None:
    cfg = AppConfig.load()
    cfg.agents["PersonalClaw"].skills = skills
    cfg.save()


def _builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(install_builtins=False),
        hooks=HookManager(),
    )


def _offered(builder: ContextBuilder, agent: str | None) -> str:
    """Everything a new session of *agent* is handed at its start."""
    return builder.build_session_context(agent=agent)


def _turn(builder: ContextBuilder, agent: str | None, text: str) -> str:
    message, _ = builder.build_message(text, is_new_session=False, agent=agent)
    return message


# ── What a turn is offered ──────────────────────────────────────────────────────────────────


def test_an_agent_with_a_skill_list_is_offered_those_skills_and_no_other(home, tmp_path):
    """🔴 Red before: the researcher, a custom agent, was offered no skill, its list unread."""
    builder = _builder(tmp_path)
    start = _offered(builder, "researcher")
    assert "**alpha**" in start
    assert "GAMMA-RULE" in start, "an always-on skill on the list is given in full"
    assert "beta" not in start

    assert "ALPHA-STEPS" in _turn(builder, "researcher", "walk me through the alpha topic")
    assert "BETA-STEPS" not in _turn(builder, "researcher", "walk me through the beta topic")


def test_a_list_on_the_default_agent_holds_a_chat_that_names_no_agent(home, tmp_path):
    """🔴 Red before: the default agent was offered every skill whatever its list said."""
    _set_default_agent_skills(["beta"])
    builder = _builder(tmp_path)
    for agent in (None, "PersonalClaw"):
        start = _offered(builder, agent)
        assert "**beta**" in start
        assert "alpha" not in start and "GAMMA-RULE" not in start
        assert "BETA-STEPS" in _turn(builder, agent, "walk me through the beta topic")
        assert "ALPHA-STEPS" not in _turn(builder, agent, "walk me through the alpha topic")


def test_with_no_list_the_default_agent_is_offered_every_skill_as_before(home, tmp_path):
    """The positive control: the default agent with no list keeps the whole library."""
    builder = _builder(tmp_path)
    start = _offered(builder, None)
    assert "**alpha**" in start and "**beta**" in start and "GAMMA-RULE" in start
    assert "ALPHA-STEPS" in _turn(builder, None, "walk me through the alpha topic")
    assert "BETA-STEPS" in _turn(builder, None, "walk me through the beta topic")


def test_with_no_list_a_custom_agent_is_offered_none_as_before(home, tmp_path):
    """The positive control for a custom agent, which carries its own instructions."""
    builder = _builder(tmp_path)
    start = _offered(builder, "plain")
    assert "alpha" not in start and "beta" not in start and "GAMMA-RULE" not in start
    assert "ALPHA-STEPS" not in _turn(builder, "plain", "walk me through the alpha topic")


def test_an_agent_on_an_agent_cli_is_held_to_no_list(home, tmp_path):
    """An agent CLI loads its own skills, where no list of PersonalClaw's holds them: its turns are
    offered what an agent with no list is. The default agent on one, by the runtime it inherits,
    keeps the whole library."""
    builder = _builder(tmp_path)
    assert "alpha" not in _offered(builder, "reviewer")
    cfg = AppConfig.load()
    cfg.agent.provider = "acp:claude-code"
    cfg.agents["PersonalClaw"].provider = ""
    cfg.agents["PersonalClaw"].skills = ["beta"]
    cfg.save()
    start = _offered(builder, None)
    assert "**alpha**" in start and "**beta**" in start


# ── What the skill tools reach ──────────────────────────────────────────────────────────────


def _call(name: str, args: dict[str, Any]) -> str:
    from personalclaw import mcp_core

    return str(mcp_core._call_tool_inner(name, args))


def test_the_skill_tools_reach_only_the_skills_the_held_list_allows(home):
    """🔴 Red before: every agent's skill tools reached the whole library."""
    from personalclaw.agents import skill_list

    token = skill_list.hold(skill_list.AgentSkills.of("researcher", ["alpha"]))
    try:
        refused = _call("skill_invoke", {"name": "beta"})
        assert (
            "the agent researcher may use only the skills on its skill list, set on the Agents "
            "page, and beta is not one of them" in refused
        )
        assert "BETA-STEPS" not in refused
        assert "ALPHA-STEPS" in _call("skill_invoke", {"name": "alpha"})
        found = _call("skill_search", {"query": "steps for the topic"})
        assert "- alpha:" in found and "beta" not in found
        assert "beta is not one of them" in _call(
            "skill_resource", {"skill": "beta", "path": "notes.md"}
        )
        assert "alpha notes" in _call("skill_resource", {"skill": "alpha", "path": "notes.md"})
    finally:
        skill_list.let_go(token)
    # Where no runtime holds a list (an agent CLI's tool server), the tools reach every skill.
    assert "BETA-STEPS" in _call("skill_invoke", {"name": "beta"})


class _Model:
    """A model that makes the scripted calls on its first request, then answers."""

    supports_tools = True
    _model = "recorder"

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls
        self.requests = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests += 1
        if self.requests == 1:
            for i, (name, args) in enumerate(self._calls):
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"c{i}",
                    title=name,
                    tool_input=json.dumps(args),
                )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.asyncio
async def test_the_runtime_holds_its_agents_skill_list_for_every_call(home, monkeypatch):
    """🔴 Red before: the researcher's ``skill_invoke`` loaded a skill its list leaves out. Built by
    the production factory over the real in-process core tools, as every native turn is."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider
    from personalclaw.providers import provider_bridge as pb

    workspace = home.parent / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    core = InProcessMcpToolProvider()
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [core])
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **_k: "recorder")
    model = _Model([("skill_invoke", {"name": "beta"}), ("skill_invoke", {"name": "alpha"})])
    monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: model)
    runtime = pb._build_native_runtime(
        use_case="chat",
        session_key="dashboard:researcher",
        agent="researcher",
        model_override=None,
        cwd=None,
    )
    runtime.set_approval_policy("yolo")
    await runtime.start()
    events = [event async for event in runtime.stream("go")]
    results = [str(e.tool_output) for e in events if e.kind == EVENT_TOOL_RESULT]
    assert len(results) == 2, results
    assert "beta is not one of them" in results[0] and "BETA-STEPS" not in results[0]
    assert "ALPHA-STEPS" in results[1]


# ── A loop's turns ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_loops_own_skills_are_reached_beside_its_agents_list(home, monkeypatch):
    """🔴 Red before: the skill tools refused nothing. A loop run as an agent with a list loads its
    plan's skills on its turns whatever the list holds, so its skill tools reach them there too (a
    skill's resources are read only through them), and any other skill is still refused. The
    runtime is built by the production factory under the key the chat runner gives a loop
    worker's runtime (``dashboard:loop-<id>``)."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider
    from personalclaw.loop import Loop
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.kinds import worker_turn
    from personalclaw.providers import provider_bridge as pb

    cfg = AppConfig.load()
    cfg.agents["researcher"].skills = ["alpha"]
    cfg.save()
    loop = loop_store.create(
        Loop(
            id="",
            name="Notes",
            kind="goal",
            task="tidy the notes",
            agent="researcher",
            skill_ids=["beta"],
        )
    )
    assert worker_turn(f"loop-{loop.id}").skill_ids == ("beta",), "what its turn loads"

    workspace = home.parent / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    core = InProcessMcpToolProvider()
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [core])
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **_k: "recorder")

    async def calls_of(session_key: str) -> list[str]:
        model = _Model(
            [
                ("skill_invoke", {"name": "beta"}),
                ("skill_resource", {"skill": "beta", "path": "notes.md"}),
                ("skill_search", {"query": "steps for the topic"}),
                ("skill_invoke", {"name": "gamma"}),
            ]
        )
        monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: model)
        runtime = pb._build_native_runtime(
            use_case="chat",
            session_key=session_key,
            agent="researcher",
            model_override=None,
            cwd=None,
        )
        runtime.set_approval_policy("yolo")
        await runtime.start()
        events = [event async for event in runtime.stream("go")]
        return [str(e.tool_output) for e in events if e.kind == EVENT_TOOL_RESULT]

    worker = await calls_of(f"dashboard:loop-{loop.id}")
    assert len(worker) == 4, worker
    assert "BETA-STEPS" in worker[0]
    assert "beta notes" in worker[1]
    assert "- beta:" in worker[2]
    assert "gamma is not one of them" in worker[3]
    # A chat of the same agent is held to its list alone.
    chat = await calls_of("dashboard:a-chat")
    assert "beta is not one of them" in chat[0] and "BETA-STEPS" not in chat[0]


def test_the_loop_worker_carries_no_list_and_its_old_seed_is_cleared(home, tmp_path):
    """🔴 Red before: the built-in loop worker was seeded with a list of the one skill that repeats
    its own instructions. Read now, that list would hold every loop's worker to that skill. It
    carries none, so a loop's worker is offered and reaches what it always was, and the seed an
    install wrote to ``config.json`` is cleared once; any other list is kept."""
    from personalclaw.agents.defaults import (
        LOOP_WORKER_AGENT_NAME,
        RETIRED_SEEDED_LOOP_WORKER_SKILLS,
        make_loop_worker_profile,
    )
    from personalclaw.agents.skill_list import agent_skills
    from personalclaw.config.migrations import apply_config_migrations

    assert make_loop_worker_profile(AgentProfile).skills == []
    cfg = AppConfig.load()
    cfg.agents[LOOP_WORKER_AGENT_NAME].skills = list(RETIRED_SEEDED_LOOP_WORKER_SKILLS)
    assert apply_config_migrations(cfg) is True
    assert cfg.agents[LOOP_WORKER_AGENT_NAME].skills == []
    assert not agent_skills(LOOP_WORKER_AGENT_NAME, cfg).listed
    assert apply_config_migrations(cfg) is False, "cleared once"
    cfg.save()
    # The positive control: its turns are offered no skill up front, as before.
    start = _offered(_builder(tmp_path), LOOP_WORKER_AGENT_NAME)
    assert "alpha" not in start and "beta" not in start and "GAMMA-RULE" not in start
    cfg.agents[LOOP_WORKER_AGENT_NAME].skills = ["loop-worker", "alpha"]
    apply_config_migrations(cfg)
    assert cfg.agents[LOOP_WORKER_AGENT_NAME].skills == ["loop-worker", "alpha"]


# ── Where else the list is said ─────────────────────────────────────────────────────────────


def test_the_skills_page_names_only_the_agents_a_list_holds(home):
    """🔴 Red before: the Skills page said the agent CLI's agent used ``alpha``, from a list that
    holds it nowhere."""
    from personalclaw.dashboard.handlers.skills import _loaded_by_agents

    used = _loaded_by_agents(["alpha", "beta", "gamma"])
    assert used["alpha"] == ["researcher"]
    assert used["gamma"] == ["researcher"]
    assert used["beta"] == []


def test_no_copy_of_the_agents_settings_is_left_that_nothing_reads():
    """🔴 Red before: the runtime's definition carried the agent's memory store, working folder,
    approval mode and triggers, and the session bindings its skills, and nothing read them: the
    runtime is handed those by the session it is built for and the turn's context. The runtime
    also published its agent's name for each call, for a tool that no longer reads it."""
    from personalclaw import mcp_core
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.agents.skill_list import AgentSkills
    from personalclaw.config.loader import ResolvedBindings

    carried = {f.name for f in dataclasses.fields(AgentRuntimeDefinition)}
    assert not carried & {"memory_store", "workspace_dir", "approval_mode", "triggers"}, carried
    # The one it keeps is the list the runtime holds for its skill tools.
    assert isinstance(AgentRuntimeDefinition(name="a").skills, AgentSkills)
    assert "skills" not in {f.name for f in dataclasses.fields(ResolvedBindings)}
    assert not hasattr(mcp_core, "set_current_agent_id")
