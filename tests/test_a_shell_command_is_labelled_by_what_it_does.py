"""A shell call's risk and its facets are what its command does, in every runtime.

The command decides a shell call's risk, whatever the shell tool declares: the platform's
``bash`` declares DESTRUCTIVE because a shell can do anything, and an ordinary read it runs is
still a read. A command the screen cannot vouch for is "Not checked": never "Safe", which would
let Trust reads run it unasked, and never "Destructive", which would claim something nobody
measured. The approval card, the channel's prompt and the risk words all say the same thing,
because the risk and the facets come from one reading of the call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_acp_permission_authority import (
    _context_builder,
    _drive,
    _make_state,
    _session,
    _set_stream,
)

from personalclaw.approval_brief import RISK_LABELS, compose_approval_brief, entry_approval_brief
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.task_modes import resolve_effective_risk

#: The effective risk of a shell command the screen could not vouch for.
UNCHECKED = "unchecked"

#: The shapes a shell call arrives in: the platform's own `bash`, which declares DESTRUCTIVE,
#: and an agent CLI's shell, which declares nothing.
SHELLS = [
    pytest.param("destructive", "bash", "", id="platform-bash"),
    pytest.param("", "execute_bash", "", id="cli-shell-name"),
    pytest.param("", "Terminal", "execute", id="cli-execute-kind"),
]

READ = "ls ~/Notes ~ 2>/dev/null | head -50"
WRITE = "cd ~ && printf 'hello\\n' > Documents/note.txt && ls -l Documents/note.txt"
DELETE = "rm -rf build"
UNREAD = "rg TODO ~/Notes"


@pytest.mark.parametrize("declared,title,kind", SHELLS)
@pytest.mark.parametrize(
    "command,risk",
    [
        (READ, "safe"),
        (WRITE, "caution"),
        (DELETE, "destructive"),
        (UNREAD, UNCHECKED),
    ],
)
def test_the_command_decides_a_shell_calls_risk(declared, title, kind, command, risk):
    assert resolve_effective_risk(declared, title, kind, {"command": command}) == risk


@pytest.mark.parametrize("declared,title,kind", SHELLS)
def test_a_shell_call_whose_command_never_arrived_is_never_safe(declared, title, kind):
    assert resolve_effective_risk(declared, title, kind, {}) == (declared or "caution")


def test_a_program_the_screen_only_describes_is_not_checked():
    assert resolve_effective_risk("destructive", "bash", "", {"command": "cp a b"}) == UNCHECKED


def test_a_declared_tool_that_is_not_a_shell_keeps_its_declaration():
    assert resolve_effective_risk("destructive", "memory_forget", "", {"command": "ls"}) == (
        "destructive"
    )
    assert resolve_effective_risk("caution", "write_file", "", {"path": "x"}) == "caution"


def test_an_unchecked_command_is_said_to_be_not_checked():
    assert RISK_LABELS[UNCHECKED] == "Not checked"


def _bash(command: str) -> SimpleNamespace:
    return SimpleNamespace(
        title="bash",
        tool_kind="",
        tool_input={"command": command},
        tool_purpose="",
        risk_level="destructive",
        tool_meta={},
    )


@pytest.mark.parametrize(
    "command,summary",
    [
        (READ, "Reads only · Risk: Safe"),
        (WRITE, "Can: writes files · Risk: Caution"),
        (DELETE, "Can: writes files · Risk: Destructive"),
        (UNREAD, "Can: runs a command · Risk: Not checked"),
        ("make build > build.log", "Can: writes files, runs a command · Risk: Not checked"),
        ("git remote show origin", "Can: uses the network · Risk: Caution"),
    ],
)
def test_a_channel_prompt_says_what_the_command_does(command, summary):
    brief = compose_approval_brief(_bash(command))
    assert brief is not None
    assert brief["summary"] == summary


@pytest.mark.parametrize("command", [READ, WRITE, DELETE, UNREAD])
def test_a_registered_approval_tells_the_channel_what_the_card_shows(command):
    """The pending entry carries the radius composed from the RAW call; the channel prompt for
    it says what the brief composed from the event says."""
    from personalclaw.approval_brief import call_blast_radius

    event = _bash(command)
    entry = {
        "tool": "bash",
        "tool_input": json.dumps(event.tool_input),
        "tool_purpose": "",
        "risk": resolve_effective_risk("destructive", "bash", "", event.tool_input),
        "blast_radius": call_blast_radius(event),
    }
    from_entry = entry_approval_brief(entry)
    from_event = compose_approval_brief(event)
    assert from_entry is not None and from_event is not None
    assert from_entry["summary"] == from_event["summary"]
    assert from_entry.get("blastRadius") == from_event.get("blastRadius")


# ── Trust reads, end to end through the chat's approval gate ─────────────────────────────────────


def _bash_permission(command: str) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="bash",
        tool_kind="",
        request_id="req-1",
        tool_input=json.dumps({"command": command}),
        risk_level="destructive",
    )


async def _trust_reads_turn(tmp_path, command: str):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    session = _session(task_mode="agent", trust=False)
    session._trust_reads = True
    _set_stream(
        client,
        [_bash_permission(command), LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
    )
    await _drive(state, session, answer="denied")
    return session, client


def _card(session) -> dict:
    cards = [m for m in session.messages if m.get("role") == "permission"]
    assert len(cards) == 1, "no approval card was raised"
    return json.loads(cards[0]["cls"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        READ,
        "cd ~ && grep -rniE 'todo|today' Notes Documents 2>/dev/null | head -20",
        "git status -sb 2>&1",
    ],
)
async def test_trust_reads_runs_a_read_without_asking(tmp_path, command):
    session, client = await _trust_reads_turn(tmp_path, command)
    client.approve_tool.assert_awaited_once_with("req-1")
    assert not [m for m in session.messages if m.get("role") == "permission"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,risk,radius",
    [
        (
            WRITE,
            "caution",
            {
                "writes": True,
                "network": False,
                "shell": False,
                "saysReadOnly": False,
                "readOnly": False,
            },
        ),
        (
            "sort -o notes.txt notes.txt",
            "caution",
            {
                "writes": True,
                "network": False,
                "shell": False,
                "saysReadOnly": False,
                "readOnly": False,
            },
        ),
        (
            "find . -name '*.tmp' -delete",
            "destructive",
            {
                "writes": True,
                "network": False,
                "shell": False,
                "saysReadOnly": False,
                "readOnly": False,
            },
        ),
        (
            UNREAD,
            UNCHECKED,
            {
                "writes": False,
                "network": False,
                "shell": True,
                "saysReadOnly": False,
                "readOnly": False,
            },
        ),
    ],
)
async def test_trust_reads_still_asks_for_a_command_that_is_not_a_read(
    tmp_path, command, risk, radius
):
    session, client = await _trust_reads_turn(tmp_path, command)
    client.approve_tool.assert_not_awaited()
    card = _card(session)
    assert card["risk"] == risk
    assert card["is_read_only"] == ""
    assert card["blast_radius"] == radius
