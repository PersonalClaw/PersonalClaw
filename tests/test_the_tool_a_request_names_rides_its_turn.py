"""The tool a request plainly needs carries its full schema that turn, inside the window's budget.

A request to set up an automation — "When a new PDF lands in ~/Documents/Home/Kitchen, summarise it
into my kitchen note." — went to a local model with ``automation_create`` as one line of the
catalog and no schema, so the model guessed its arguments, was refused, and guessed again for
twenty minutes. Replayed against the ranking that ranks on the request alone, with the schema
budget at an eighth of a 32,768-token window, the retry that said "set that up as an automation"
still left it out while it ranked FIRST of 25 scored tools. The path in the request had forced
in every tool whose name contained "read", "file", "dir" or "edit" — 27 of them, ``task_ready``
among them for "ready" — ahead of anything ranked, and they took the whole budget.

What holds now:

* a hint names tools by the words of their names, never by part of a word, and there is no path
  hint: the file tools a path calls for are the core ones, which ride every turn;
* a cadence ("every Monday … message me") and an event ("when a new PDF lands in …", "an
  automation") name ``automation_create``;
* the answer to the agent's question keeps, for that one turn, the tools the request it answers
  plainly needed;
* a call that names an argument the tool does not take is told the ones it does.
"""

from __future__ import annotations

import pytest

from personalclaw.agents.native.tool_retrieval import ToolRetriever, schema_budget_chars
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition
from personalclaw.validation import MCP_AUTOMATION_SCHEMAS, ValidationError, validate_tool_args

_CORE = ("bash", "read_file", "write_file", "edit_file", "grep", "glob", "list_dir")

#: A filesystem server scoped to a notes folder, as a user configures one.
_NOTES_SERVER = tuple(
    f"mcp/notes/{n}"
    for n in (
        "read_file",
        "read_text_file",
        "read_media_file",
        "read_multiple_files",
        "write_file",
        "edit_file",
        "create_directory",
        "list_directory",
        "list_directory_with_sizes",
        "directory_tree",
        "move_file",
        "search_files",
        "get_file_info",
        "list_allowed_directories",
    )
)


def _tool(name: str, size: int, description: str = "") -> ToolDefinition:
    """A tool whose schema is about ``size`` characters, the way a real one's grows: arguments."""
    properties = {
        f"arg{n}": {"type": "string", "description": "x" * 80} for n in range(max(1, size // 120))
    }
    return ToolDefinition(
        name=name,
        description=description or f"Does {name.replace('_', ' ')}.",
        parameters={"type": "object", "properties": properties},
        requires_approval=False,
        risk_level=RiskLevel.SAFE,
    )


def _catalog() -> list[ToolDefinition]:
    """About 130 tools, shaped like a configured install's: the core, the automation and
    scheduling tools at their real sizes, a notes filesystem server, a code host's and a wiki's
    file-named tools, the task tools, and a long tail."""
    tools = [_tool(name, 600) for name in _CORE]
    tools += [
        _tool("automation_create", 2640),
        _tool("automation_run", 450),
        _tool("automation_list", 500),
        _tool("set_onetime_task", 2000),
        _tool("set_recurring_task", 1400),
        _tool("task_ready", 700),
        _tool("task_search", 800),
        _tool("knowledge_search", 700),
        _tool("workflow_edit", 1100),
        _tool("workflow_edit_preview", 900),
        _tool("web_fetch", 600),
        _tool("mcp/code/get_file_contents", 900),
        _tool("mcp/code/get_pull_request_files", 700),
        _tool("mcp/code/search_repositories", 800),
        _tool("mcp/wiki/read_wiki_contents", 500),
        _tool("mcp/wiki/read_wiki_structure", 500),
    ]
    tools += [_tool(name, 800) for name in _NOTES_SERVER]
    tools += [_tool(f"library_tool_{n}", 700) for n in range(100)]
    return tools


_BUDGET = schema_budget_chars(32_768)


def _select(retriever: ToolRetriever, request: str) -> set[str]:
    chosen = retriever.select(request, budget_chars=_BUDGET)
    names = {d.name for d in chosen}
    assert sum(retriever._chars[n] for n in names) <= _BUDGET, "the window's budget still holds"
    assert set(_CORE) <= names, "the core rides every turn"
    return names


@pytest.mark.parametrize(
    "request_text",
    [
        "When a new PDF lands in ~/Documents/Home/Kitchen, summarise it into my kitchen note.",
        "Please set that up as an automation: when a new PDF lands in ~/Documents/Home/Kitchen, "
        "summarise it into ~/Notes/Garden/Home/kitchen-reno.md.",
        "Whenever a quote arrives in ~/Documents/Home/Kitchen, add it to the comparison.",
    ],
)
def test_a_request_for_an_automation_carries_automation_create(request_text):
    assert "automation_create" in _select(ToolRetriever(_catalog()), request_text)


def test_a_weekday_reminder_carries_the_tools_that_remind():
    names = _select(
        ToolRetriever(_catalog()),
        "Every Monday, Tuesday and Thursday at 15:10, message me on Telegram: 'Pickup at 15:30.'",
    )
    assert {"automation_create", "set_recurring_task"} <= names, sorted(names)


def test_a_path_names_no_tool_by_part_of_a_word():
    """A path is not a request for every tool whose name contains "read" or "file"."""
    names = _select(ToolRetriever(_catalog()), "Look at ~/Notes/Garden/Home/kitchen-reno.md")
    forced = names - set(_CORE) - {"tool_search", "tool_schema"}
    assert not forced & {"task_ready", "workflow_edit", "workflow_edit_preview"}, sorted(forced)
    assert not forced & set(_NOTES_SERVER), sorted(forced)
    assert not forced & {"mcp/code/get_file_contents", "mcp/wiki/read_wiki_contents"}


def test_a_url_does_not_pull_every_search_tool():
    names = _select(ToolRetriever(_catalog()), "What does https://example.org/notes say?")
    assert "web_fetch" in names
    assert not names & {"mcp/code/search_repositories", "task_search", "knowledge_search"}


def test_the_answer_to_a_question_keeps_the_tool_its_request_needed():
    """The agent asked which note; the answer names none of the task, and the task stays."""
    retriever = ToolRetriever(_catalog())
    _select(
        retriever,
        "When a new PDF lands in ~/Documents/Home/Kitchen, summarise it into my kitchen note.",
    )
    assert "automation_create" in _select(retriever, "It's ~/Notes/Garden/Home/kitchen-reno.md.")
    # For that one turn: a third turn that asks for nothing carries nothing.
    assert "automation_create" not in _select(retriever, "Thanks, that is all.")


def test_a_new_request_replaces_what_the_last_one_carried():
    retriever = ToolRetriever(_catalog())
    _select(retriever, "Every Monday at 9, message me: 'Standup.'")
    names = _select(retriever, "What does https://example.org/notes say?")
    assert "web_fetch" in names
    assert "set_recurring_task" not in names


def test_a_call_with_an_argument_the_tool_does_not_take_is_told_the_ones_it_does():
    schema = MCP_AUTOMATION_SCHEMAS["automation_create"]
    with pytest.raises(ValidationError) as caught:
        validate_tool_args({"trigger_type": "file_watch", "name": "Kitchen"}, schema)
    message = str(caught.value)
    assert message.startswith("trigger_type: unknown field for tool 'automation_create'")
    assert "it takes: name, when, message, say, via, kind, spec" in message
