"""A tool call's row keeps its input as the card showed it, so a reload shows the same card.

A tool row kept its call's input only as a string cut at 4,000 characters. The live ``tool_call``
frame carried the redacted object too, but only up to 8,000 characters of it in all; past that the
object was dropped, the card fell back to the string, and the row kept that string alone. A call
whose input ran long (a command carrying a script, a file write) was stored as JSON cut off
mid-string, and a reload rebuilt the card from it (#5729). In an owner chat, several commands'
stored inputs were cut this way and no longer parsed.

Now each value of the object is bounded instead of the object dropped, the row keeps the object the
frame sends (``meta.input_obj``), and the string stays as the preview. Driven through the real chat
runner and the real session read; only the runtime is a fake.
"""

from __future__ import annotations

import json

import pytest
from test_chat_turn_outcome import _detail, _frames, _session, _streaming, _turn

from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_CALL_UPDATE,
    EVENT_TOOL_RESULT,
    AgentEvent,
)

#: A command carrying a script, over 10,000 characters long.
SCRIPT = "set -e\n" + "".join(f"echo 'step {i:05d} of the migration'\n" for i in range(334))
TOKEN = "ghp_" + "A1b2C3d4E5" * 4


def _stream_calling(tool_input: dict, *, later: bool = False):
    """One turn calling ``bash`` with *tool_input*: on its opening frame, as the native runtime
    sends it, or (*later*) on an update, as an agent CLI's adapter sends the object after an
    empty opening frame."""

    async def stream():
        if later:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id="tc-1", title="bash", tool_input="{}"
            )
            yield AgentEvent(
                kind=EVENT_TOOL_CALL_UPDATE,
                tool_call_id="tc-1",
                title="bash",
                tool_input=json.dumps(tool_input),
                tool_input_obj=tool_input,
            )
        else:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id="tc-1", title="bash", tool_input=tool_input
            )
        yield AgentEvent(kind=EVENT_TOOL_RESULT, tool_call_id="tc-1", tool_output="done")
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Migrated.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    return stream


async def _live_and_reloaded(tmp_path, tool_input: dict, *, later: bool = False):
    """The input object the live card was last sent, and the one a reload reads off its row."""
    state = _streaming(tmp_path, _stream_calling(tool_input, later=later))
    await _turn(state, _session(state), "run the migration")
    live = [f["input"] for f in _frames(state, "tool_call") if f.get("tool_call_id") == "tc-1"][-1]
    rows = [
        m
        for m in (await _detail(state))["messages"]
        if m.get("role") == "tool" and (m.get("meta") or {}).get("tool_call_id") == "tc-1"
    ]
    assert len(rows) == 1, rows
    return live, rows[0]["meta"].get("input_obj")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_a_long_command_shows_the_same_fields_live_and_after_a_reload(tmp_path) -> None:
    assert len(SCRIPT) > 10_000
    live, reloaded = await _live_and_reloaded(tmp_path, {"command": SCRIPT, "timeout": 120})

    assert live is not None, "the live card has the call's fields"
    assert reloaded == live, "a reload shows the card the turn showed"
    assert reloaded["timeout"] == 120
    assert reloaded["command"].startswith(SCRIPT[:4000]), "whole past where the preview is cut"
    assert reloaded["command"].endswith(f"… {len(SCRIPT) - 8000:,} more characters")
    json.dumps(reloaded)  # it is an object, not a string cut mid-JSON


@pytest.mark.asyncio
async def test_a_short_input_is_kept_whole(tmp_path) -> None:
    live, reloaded = await _live_and_reloaded(tmp_path, {"command": "ls -la", "timeout": 5})

    assert live == reloaded == {"command": "ls -la", "timeout": 5}


@pytest.mark.asyncio
async def test_the_kept_input_is_redacted_as_the_live_one_is(tmp_path) -> None:
    command = f"export GH_TOKEN={TOKEN}\n{SCRIPT}"
    live, reloaded = await _live_and_reloaded(tmp_path, {"command": command})

    assert TOKEN not in json.dumps(live) and TOKEN not in json.dumps(reloaded)
    assert "[REDACTED: credential]" in reloaded["command"]
    assert reloaded == live


@pytest.mark.asyncio
async def test_a_secret_where_the_value_is_cut_is_masked_whole(tmp_path) -> None:
    """Redacted before it is bounded: a cut through a secret would leave its start unmasked."""
    command = "#" * (8000 - 10) + TOKEN + "\n" + SCRIPT
    live, reloaded = await _live_and_reloaded(tmp_path, {"command": command})

    assert TOKEN[:10] not in json.dumps(reloaded) and TOKEN[:10] not in json.dumps(live)


@pytest.mark.asyncio
async def test_an_object_an_update_carries_is_kept_on_the_row(tmp_path) -> None:
    """An agent CLI's adapter opens the call empty and sends its input on an update."""
    live, reloaded = await _live_and_reloaded(
        tmp_path, {"command": SCRIPT, "timeout": 120}, later=True
    )

    assert reloaded == live and reloaded["timeout"] == 120
    assert reloaded["command"].endswith(f"… {len(SCRIPT) - 8000:,} more characters")


@pytest.mark.asyncio
async def test_an_input_of_a_great_many_values_falls_back_to_its_preview(tmp_path) -> None:
    """A backstop for a pathological call: past 256 KB in all, neither the card nor the row keeps
    the object, and both show the preview string, as every card did before."""
    tool_input = {f"part_{i:02d}": SCRIPT for i in range(40)}  # 40 values of 8,000 kept: 320 KB
    live, reloaded = await _live_and_reloaded(tmp_path, tool_input)

    assert (live, reloaded) == (None, None)
