"""A room member bound to an agent CLI answers from that CLI, as the agent that was chosen.

Rooms › Members › Add a member offers the agents a ready agent CLI lists, and adding one saves the
binding the chat's agent defaults use (``provider: acp:<cli>`` + ``provider_agent``). The member's
own session must then run on that CLI. Driven here through the shipped
``rooms.turn.run_member_turn``, the real session manager, the real provider bridge and the real
ACP client, against a scripted agent CLI that records every frame it is sent.

Red before the fix: the member's session was opened with the binding's NAME only. The CLI was
asked to switch to a mode named after the binding (``acp-scripted-cli-careful-coder``), which no
CLI offers, instead of the agent that was picked; and with no runtime named, which program
answered was left to the model axis' fallback over whatever else was registered.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from test_a_loop_runs_on_the_agent_cli_its_owner_chose import _AGENT

from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
from personalclaw.llm.acp_agent import _factory as acp_factory
from personalclaw.llm.registry import ProviderEntry, get_default_registry, reset_default_registry
from personalclaw.rooms import store, turn
from personalclaw.session import SessionManager

RUNTIME = "acp:scripted-cli"
CHOSEN = "careful-coder"
#: The binding the member picker saves for that agent (``ensureBindableAgentName``'s name).
MEMBER = "acp-scripted-cli-careful-coder"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The home, the user's own folder and the operator ceiling are this test's own."""
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path / "home")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "home").mkdir()
    (tmp_path / "user").mkdir()
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


@pytest.fixture
def scripted_cli(tmp_path) -> Path:
    """The scripted agent CLI, set up here as ``acp:scripted-cli``; returns its record file."""
    script = tmp_path / "scripted_cli.py"
    script.write_text(_AGENT)
    record = tmp_path / "wire.jsonl"
    reset_default_registry()
    get_default_registry().register_type(ACP_AGENT_CAPABILITY, acp_factory)
    get_default_registry().register_entry(
        ProviderEntry(
            name=RUNTIME,
            type=ACP_AGENT_CAPABILITY.type,
            model="",
            options={"command": [sys.executable, str(script), str(record)], "dialect": "default"},
            credential=None,
            declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
        )
    )
    try:
        yield record
    finally:
        reset_default_registry()


@pytest.fixture
def config(monkeypatch) -> AppConfig:
    """Rooms on, and the member's binding on the scripted CLI as the agent it offered."""
    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {MEMBER: AgentProfile(provider=RUNTIME, provider_agent=CHOSEN)}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    return cfg


def _wire(record: Path, method: str) -> list[dict]:
    if not record.exists():
        return []
    rows = [json.loads(line) for line in record.read_text().splitlines() if line]
    return [r for r in rows if r.get("kind") == "received" and r.get("method") == method]


def _spawns(record: Path) -> int:
    if not record.exists():
        return 0
    return sum(1 for line in record.read_text().splitlines() if '"spawn"' in line)


@pytest.mark.asyncio
async def test_a_member_on_an_agent_cli_takes_its_turn_on_that_cli(config, scripted_cli):
    sessions = SessionManager(config, provider_factory=config.create_provider_factory())
    room = store.create_room("Is the release ready to ship?")
    store.add_member(room.id, MEMBER, role_blurb="careful reviewer")
    store.append_message(room.id, role="user", content="What is left before we ship?")
    try:
        reply = await asyncio.wait_for(turn.run_member_turn(sessions, room.id, MEMBER), timeout=30)
    finally:
        await sessions.close_all()

    assert _spawns(scripted_cli) == 1, "the member's turn did not run on its agent CLI"
    assert [f["params"].get("modeId") for f in _wire(scripted_cli, "session/set_mode")] == [CHOSEN]
    prompts = _wire(scripted_cli, "session/prompt")
    assert len(prompts) == 1
    assert "What is left before we ship?" in json.dumps(prompts[0]["params"])
    assert "starting the first task" in reply
    said = [m for m in store.read_messages(room.id) if m.get("speaker") == MEMBER]
    assert said and "starting the first task" in said[-1]["content"]


@pytest.mark.asyncio
async def test_a_native_member_still_takes_its_turn_in_personalclaw(monkeypatch, config):
    """Ordinary content still works: a native binding opens its session by name, with no runtime."""
    config.agents = {"talk-editor": AgentProfile()}
    asked: list[dict] = []

    class _Sessions:
        async def get_or_create(self, key, **kw):
            asked.append(kw)
            raise RuntimeError("stop here: only the binding is under test")

        def release(self, key):
            return None

    room = store.create_room("Is the live demo worth the risk?")
    store.add_member(room.id, "talk-editor")
    with pytest.raises(RuntimeError, match="stop here"):
        async with turn.member_session(_Sessions(), room.id, "talk-editor"):
            pass
    assert asked and asked[0]["agent"] == "talk-editor"
    assert not asked[0].get("provider_kind"), "a native member names no agent CLI to run on"
