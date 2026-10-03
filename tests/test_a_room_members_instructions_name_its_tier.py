"""A room member is told what its tools may do, in its own instructions, on every turn.

The members panel says "Read-only tools", and the room holds such a member to that tier, but the
member's own prompt said only its name, the room, its role and the transcript. So a read-only
member offered "I can make that correction myself if you say so", a peer told it to go ahead, and
the owner learned it could not only once she asked and its write was refused. Its instructions now
name its tier and what that tier may not do, in the words its tier is shown by, outside the fenced
transcript where only instructions are.

Driven through the shipped ``rooms.turn.run_member_turn``; the member's provider only records the
prompt it is handed.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.llm.events import EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.rooms import store, turn

MEMBER = "talk-editor"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


@pytest.fixture
def enabled(monkeypatch):
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {MEMBER: AgentProfile()}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    return cfg


class _Recorder:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream(self, message: str):
        self.prompts.append(message)
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Cut the live demo; play the recording.")


class _Sessions:
    def __init__(self) -> None:
        self.provider = _Recorder()
        self.opened = 0

    async def get_or_create(self, key, agent=None, **kwargs):
        self.opened += 1
        return self.provider, self.opened == 1, False

    def release(self, key, *, cleanup=False):
        return None


def _prompts(narrowing: dict | None) -> list[str]:
    """The prompts of the member's first and second turns."""
    room = store.create_room("Is the live crash demo in section 6 worth the risk?")
    store.add_member(room.id, MEMBER, role_blurb="tough editor", profile_narrowing=narrowing)
    sessions = _Sessions()
    for said in ("Is the demo worth it?", "And the incident notes line?"):
        store.append_message(room.id, role="user", content=said, speaker="")
        asyncio.run(turn.run_member_turn(sessions, room.id, MEMBER))
    return sessions.provider.prompts


def _instructions(prompt: str) -> str:
    """What the member is told, outside the fenced transcript."""
    return prompt.split("<untrusted_content", 1)[0]


READ_ONLY = (
    "Your tools in this room are read-only: you can read and search, and you may not write, edit, "
    "move or delete anything or run a command. When something should change, say exactly what, "
    "and leave the change to the human: do not offer to make it yourself."
)


def test_a_read_only_member_is_told_it_is_read_only_on_every_turn(enabled):
    """🔴 Before: neither prompt said anything of its tools."""
    first, second = _prompts(None)

    assert READ_ONLY in _instructions(first), first
    assert READ_ONLY in _instructions(second), second


def test_a_member_declared_read_only_is_told_the_same(enabled):
    first, _second = _prompts({"tool_grants": "read"})

    assert READ_ONLY in _instructions(first)


def test_a_member_that_may_change_things_is_told_so_and_not_that_it_is_read_only(enabled):
    first, second = _prompts({"tool_grants": "read_write"})

    for prompt in (first, second):
        said = _instructions(prompt)
        assert "Your tools in this room can read and change things." in said, said
        assert "Your tools in this room are read-only" not in said


def test_a_member_held_to_named_tools_is_told_which(enabled):
    first, _second = _prompts({"tool_grants": "custom", "tool_allowlist": ["read_file", "grep"]})

    said = _instructions(first)
    assert (
        "Your tools in this room are only these: read_file, grep. Any other tool is refused, so "
        "do not offer what they cannot do."
    ) in said, said


def test_a_member_with_no_tools_is_told_it_has_none(enabled):
    first, _second = _prompts({"tool_grants": "custom"})

    said = _instructions(first)
    assert (
        "You have no tools in this room: you may not read or change anything yourself. When "
        "something should change, say exactly what, and leave the change to the human."
    ) in said, said
