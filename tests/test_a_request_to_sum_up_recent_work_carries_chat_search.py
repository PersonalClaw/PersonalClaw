"""A request to sum up recent work, or about something said before, carries ``chat_search``.

The catalog lists every tool by name, but only some carry their full schema on a turn of a large
catalog, and a handoff's turn on a native install went to the error tracker, the notes and the code
host while what only her chats held went unread. A handoff, a standup, a weekly review, "this week",
"what did we decide" and "do you remember" now name the tool, so its schema rides those turns,
inside the window's budget, while a request with none of that does not spend the room on it.
"""

from __future__ import annotations

import pytest
from test_the_tool_a_request_names_rides_its_turn import _catalog, _select

from personalclaw.agents.native.tool_retrieval import ToolRetriever
from personalclaw.mcp_memory import _list_tools
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition
from personalclaw.validation import offered_schema, tool_field_schema


def _chat_search() -> ToolDefinition:
    """The tool as the memory provider offers it, at its real size."""
    tool = next(t for t in _list_tools() if t["name"] == "chat_search")
    return ToolDefinition(
        name="chat_search",
        description=tool["description"],
        parameters=offered_schema(tool["inputSchema"], tool_field_schema("chat_search")),
        requires_approval=False,
        risk_level=RiskLevel.SAFE,
    )


def _retriever() -> ToolRetriever:
    return ToolRetriever([*_catalog(), _chat_search()])


@pytest.mark.parametrize(
    "request_text",
    [
        "Write my on-call handoff for the incoming primary. Open error-tracker issues with more "
        "than 100 events in the last 7 days, grouped by whether they are new this week.",
        'Draft my async standup. "Yesterday" means the last working day before today.',
        "Run my weekly review from my notes and propose five priorities for the coming week.",
        "Catch me up on the retry design.",
        "What did we decide about the retry ceiling?",
        "Do you remember the deploy freeze dates I gave you?",
        "Summarise what came up in our earlier chats about the carrier outages.",
    ],
)
def test_a_request_about_recent_work_or_an_earlier_chat_carries_chat_search(request_text):
    """🔴 Red on integration: no hint names a tool that searches chats."""
    assert "chat_search" in _select(_retriever(), request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        "What does https://example.org/notes say?",
        "Look at ~/Notes/Garden/Home/kitchen-reno.md",
        "When a new PDF lands in ~/Documents/Home/Kitchen, summarise it into my kitchen note.",
    ],
)
def test_a_request_with_none_of_that_does_not_spend_the_room_on_it(request_text):
    assert "chat_search" not in _select(_retriever(), request_text)
