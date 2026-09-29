"""`automation_create` tells the agent which automations wait for the owner's Allow, and that is the
rule `triggers.tools.create` applies.

Before, the tool's description (what the model reads before it calls it) ended
"It does not run until the owner allows it on the Triggers page, so tell them it is waiting." A
`say` automation — words sent as written, a read-only `send-message` action — needs no grant and
was live at once, and the same call's result said so ("it is active now"). A model that trusted the
description would tell the owner a reminder was waiting when it was already armed.
"""

from __future__ import annotations

import pytest

from personalclaw.mcp_automation import _list_tools
from personalclaw.triggers import tools as T
from personalclaw.triggers.store import TriggerStore

EVERY_MONDAY = {"kind": "cron", "expr": "0 18 * * 1"}


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _description() -> str:
    (tool,) = [t for t in _list_tools() if t["name"] == "automation_create"]
    return str(tool["description"])


def test_the_description_does_not_say_every_automation_waits():
    """🔴 Before: "It does not run until the owner allows it on the Triggers page, so tell them it
    is waiting." for every automation."""
    said = _description()
    assert "so tell them it is waiting" not in said
    assert "One that sends `say` is active at once" in said
    assert "one that runs `message` does not run until the owner allows it" in said


def test_a_say_automation_is_active_at_once(store):
    made = T.create(
        store,
        name="Plants",
        kind="clock",
        spec=EVERY_MONDAY,
        say="Water the plants.",
        created_by="agent",
    )
    assert made.ok, made.text
    assert made.data["needs_grant"] == []
    assert "it is active now" in made.text


def test_a_message_automation_waits_for_the_owner(store):
    made = T.create(
        store,
        name="Weekly note",
        kind="clock",
        spec=EVERY_MONDAY,
        message="Summarise the week's notes.",
        created_by="agent",
    )
    assert made.ok, made.text
    assert made.data["needs_grant"] == ["Run Prompt"]
    assert "it does not run until you allow it there" in made.text
