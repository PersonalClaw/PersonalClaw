"""'Make me a button that runs …' makes something that runs: a manual automation.

Asked for a button that runs the kitchen-quote comparison, the agent drew a chat widget whose button
only posted '[UI] run_comparison' back into the chat, and said it had "created a button that
triggers a comparison". The store has had a kind for this all along (``manual``, the Triggers
page's "When you run it", run with Run now), but the chat's ``automation_create`` never named it,
the phrase router had no cue for an on-demand run ("when I run it" read as an event), and nothing
told the model a widget button cannot run anything.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import nl_kind, tools
from personalclaw.triggers.store import TriggerStore

COMPARE = "Compare the three kitchen quotes against my must-haves and my payment rule."


@pytest.mark.parametrize(
    "when",
    [
        "when I run it",
        "a button that runs the comparison",
        "on demand",
        "manually, whenever I want",
        "when I click the button",
    ],
)
def test_an_on_demand_phrase_routes_to_a_manual_automation(when):
    route = nl_kind.route(when)
    assert route.ok and route.kind == "manual", route


def test_an_event_i_wait_for_is_still_an_event():
    assert nl_kind.route("when I get a memory about the kitchen").kind == "event"


def test_the_chat_makes_a_manual_automation_that_says_how_it_runs():
    made = tools.create(
        TriggerStore(), name="Kitchen quote comparison", kind="manual", message=COMPARE
    )
    assert made.ok, made.text
    row = made.data["trigger"]
    assert row["kind"] == "manual"
    assert not row.get("expires_at"), "a button that never fires on its own has nothing to expire"
    assert "it runs only when you run it: Run now on the Triggers page" in made.text
    assert "when it runs: Its agent only reads" in made.text
    assert "it does not run until you allow it there" in made.text


def test_the_phrase_alone_makes_one_too():
    made = tools.create(TriggerStore(), name="Compare", when="when I run it", message=COMPARE)
    assert made.ok and made.data["trigger"]["kind"] == "manual", made.text


def test_the_chat_tool_names_the_manual_kind_and_what_a_widget_button_cannot_do():
    from personalclaw.mcp_automation import _list_tools

    [create] = [t for t in _list_tools() if t["name"] == "automation_create"]
    assert "'manual'" in create["description"] and "Run now" in create["description"]
    assert "A chat widget's button cannot run anything" in create["description"]
    assert "manual" in create["inputSchema"]["properties"]["kind"]["description"]


@pytest.mark.parametrize("density", ["more", "less"])
def test_the_widget_instructions_say_a_widget_button_runs_nothing(density):
    from personalclaw.prompt_providers.runtime import render_snippet_block

    text = render_snippet_block("widget-instructions", values={"density": density})
    assert "A widget button runs nothing by itself" in text
    assert 'automation_create` and `kind: "manual"' in text
