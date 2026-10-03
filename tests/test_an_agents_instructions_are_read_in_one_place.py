"""The one reader of an agent's own instructions and voice, whichever store defines the agent.

``agents.instructions.agent_instructions`` answers for an agent its profile in ``config.json``
first, its file under ``<home>/agents`` when the profile holds no instructions, and nothing
otherwise. What each path that runs an agent hands its model is read in
``test_a_named_agents_instructions_reach_its_model.py``; this reads the answers themselves.
"""

from __future__ import annotations

import json

from personalclaw.agent import agents_dir
from personalclaw.agents.instructions import AgentInstructions, agent_instructions
from personalclaw.config import loader as config_loader

OWN = "You keep the neighbourhood map. Mark every place you name with its grid square."
FILED = "You read the survey files and answer from them alone."
VOICE = "Dry and exact."


def _install(agents: dict[str, dict], *, default: str = "PersonalClaw") -> None:
    data = {
        "default_agent": default,
        "agents": {
            "PersonalClaw": {"provider": "native", "system_prompt": "", "source": "builtin"},
            **agents,
        },
    }
    config_loader.config_dir()  # where the file is: finding it makes nothing
    config_loader.config_path().write_text(json.dumps(data), encoding="utf-8")


def _agent_file(filename: str, content: object) -> None:
    agents_dir().mkdir(parents=True, exist_ok=True)
    (agents_dir() / filename).write_text(json.dumps(content), encoding="utf-8")


def test_the_profile_answers_first():
    _install({"map-keeper": {"system_prompt": OWN, "voice": VOICE}})
    _agent_file("map-keeper.json", {"name": "map-keeper", "prompt": FILED})
    assert agent_instructions("map-keeper") == AgentInstructions(OWN, VOICE)


def test_a_profile_with_no_instructions_reads_its_file():
    """A file the agents sync folded in carries its instructions in ``prompt``, which the sync
    does not copy, so the file still holds them."""
    _install({"surveyor": {"system_prompt": "", "voice": VOICE}})
    _agent_file("surveyor.json", {"name": "surveyor", "prompt": FILED, "voice": "Loud."})
    assert agent_instructions("surveyor") == AgentInstructions(FILED, VOICE)


def test_an_agent_with_only_a_file_is_read_from_it_by_name_or_file_name():
    _install({})
    _agent_file("survey-reader.json", {"system_prompt": FILED, "prompt": OWN, "voice": VOICE})
    _agent_file("other.json", {"name": "named-inside", "prompt": OWN})
    assert agent_instructions("survey-reader") == AgentInstructions(FILED, VOICE)
    assert agent_instructions("named-inside") == AgentInstructions(OWN, "")


def test_a_file_prompt_is_read_from_the_file_it_points_at(tmp_path):
    prompt = tmp_path / "surveyor.md"
    prompt.write_text(FILED, encoding="utf-8")
    _install({})
    _agent_file("surveyor.json", {"name": "surveyor", "prompt": f"file://{prompt}"})
    assert agent_instructions("surveyor").prompt == FILED


def test_a_prompt_file_that_holds_secrets_is_not_read():
    """A pointer at PersonalClaw's own credential store is refused by the sensitive-path check,
    and the agent has no instructions rather than a failed turn."""
    store = config_loader.config_dir() / ".env"
    store.write_text("PLACEHOLDER_NAME=placeholder-value\n", encoding="utf-8")
    _install({})
    _agent_file("surveyor.json", {"name": "surveyor", "prompt": f"file://{store}"})
    assert agent_instructions("surveyor") == AgentInstructions("", "")


def test_a_file_with_no_usable_prompt_answers_none():
    _install({})
    _agent_file("nulled.json", {"name": "nulled", "prompt": None})
    _agent_file("missing.json", {"name": "missing"})
    _agent_file("numeric.json", {"name": "numeric", "prompt": 12})
    (agents_dir() / "broken.json").write_text("{not json", encoding="utf-8")
    _agent_file("listed.json", ["broken"])
    for name in ("nulled", "missing", "numeric", "broken", "listed"):
        assert agent_instructions(name) == AgentInstructions("", ""), name


def test_the_default_agent_is_the_one_every_new_chat_starts_with():
    _install({"map-keeper": {"system_prompt": OWN, "voice": VOICE}}, default="map-keeper")
    assert agent_instructions(None) == AgentInstructions(OWN, VOICE)
    assert agent_instructions("") == AgentInstructions(OWN, VOICE)
    # The built-in agent, under either of its spellings, is still itself.
    assert agent_instructions("PersonalClaw") == AgentInstructions("", "")
    assert agent_instructions("personalclaw") == AgentInstructions("", "")


def test_the_runtime_config_is_no_agents_instructions():
    """``personalclaw.json`` is what the agent CLI reads, and its ``prompt`` names the shipped Chat
    prompt; read as the default agent's own, it would replace the prompt Settings → Prompts
    binds."""
    _install({})
    _agent_file("personalclaw.json", {"name": "personalclaw", "prompt": FILED})
    assert agent_instructions(None) == AgentInstructions("", "")
    assert agent_instructions("personalclaw") == AgentInstructions("", "")


def test_a_name_config_does_not_hold_is_never_answered_with_another_agents_words():
    """The runtime bindings answer such a name with the default agent's profile; the
    instructions reader answers it with nothing."""
    _install({"map-keeper": {"system_prompt": OWN, "voice": VOICE}}, default="map-keeper")
    assert agent_instructions("ghost") == AgentInstructions("", "")


def test_the_voice_is_composed_ahead_of_the_instructions():
    composed = AgentInstructions(OWN, VOICE).composed()
    assert VOICE in composed and composed.index(VOICE) < composed.index(OWN)
    assert AgentInstructions(OWN, "").composed() == OWN
    assert AgentInstructions("", "").composed() == ""
