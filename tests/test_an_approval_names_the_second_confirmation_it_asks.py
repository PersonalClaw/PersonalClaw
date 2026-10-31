"""Every approval says which second confirmation it asks before it runs, worked out once where it
is recorded, so every surface that answers it asks for the same calls.

Four kinds ask: a delete, a write outside the chat's folder, a send to other people and spending.
A tool that says nothing about what it does is taken to be able to do any of them, so a call to it
asks too, unless the call is an established read. PersonalClaw's own tools say what they do, so a
call to one of them decides on the first press unless it is one of the four.

An agent CLI's own tools declare nothing: its shell call is judged by its command and its edit by
the files it names, and any other tool of it counts as undeclared.
"""

from __future__ import annotations

import asyncio

import pytest
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_ended_owner_ends_its_approvals import _until

from personalclaw.approval_answer import YOU
from personalclaw.approval_confirms import (
    DELETES,
    OUTSIDE_WORKSPACE,
    SENDS,
    SPENDS,
    UNDECLARED,
    call_confirms,
)
from personalclaw.tool_providers.base import CONSEQUENCES_META_KEY, ToolDefinition, consequences_of

pytestmark = pytest.mark.security


def _confirms(
    tmp_path, declared, title, tool_input, *, kind="", consequences=None, own=False, named_files=()
):
    return call_confirms(
        declared,
        title,
        kind,
        tool_input,
        cwd=str(tmp_path),
        consequences=consequences,
        own=own,
        named_files=named_files,
    )


# ── PersonalClaw's own tools ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rm -rf build", (DELETES,)),
        ('eval "$X"', (DELETES,)),  # a command the screen could not read can delete
        ("echo hi > notes.txt", ()),  # a write inside the chat's folder
        ("echo hi > /etc/hosts.bak", (OUTSIDE_WORKSPACE,)),
        ("ls -la", ()),
    ],
)
def test_a_shell_call_is_judged_by_its_command(tmp_path, command, expected):
    got = _confirms(tmp_path, "destructive", "bash", {"command": command}, own=True)
    assert got == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("notes.md", ()),
        ("/etc/notes.md", (OUTSIDE_WORKSPACE,)),
        ("../outside.md", (OUTSIDE_WORKSPACE,)),
    ],
)
def test_a_file_write_is_judged_by_its_path(tmp_path, path, expected):
    got = _confirms(tmp_path, "caution", "write_file", {"path": path}, own=True)
    assert got == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('echo hi > "$OUT"', (OUTSIDE_WORKSPACE,)),
        ('cp notes.md "$DEST"', (DELETES, OUTSIDE_WORKSPACE)),
        ("tee -a $LOG < notes.md", (DELETES, OUTSIDE_WORKSPACE)),
        # A command the screen could not read can do anything a shell can: it may destroy. Its
        # write is read from its parts, and goes where a substitution prints, which it cannot name.
        ("echo hi > $(mktemp)", (DELETES, OUTSIDE_WORKSPACE)),
    ],
)
def test_a_shell_write_whose_target_the_screen_cannot_read_counts_as_outside(
    tmp_path, command, expected
):
    got = _confirms(tmp_path, "destructive", "bash", {"command": command}, own=True)
    assert got == expected


@pytest.mark.parametrize(
    "tool_input",
    [{}, {"path": ""}, {"path": 3}, {"path": None}, "{not json", "", '{"other": "x"}'],
)
def test_a_file_write_that_names_no_path_this_reading_can_follow_counts_as_outside(
    tmp_path, tool_input
):
    assert _confirms(tmp_path, "caution", "write_file", tool_input, own=True) == (
        OUTSIDE_WORKSPACE,
    )


def test_a_built_in_change_that_is_none_of_the_four_decides_on_the_first_press(tmp_path):
    assert _confirms(tmp_path, "caution", "task_create", {"title": "x"}, own=True) == ()


def test_a_send_asks_and_a_call_that_only_tells_the_owner_does_not(tmp_path):
    sends = (SENDS,)
    assert _confirms(
        tmp_path, "caution", "notify", {"text": "hi", "user": "U1"}, own=True, consequences=sends
    ) == (SENDS,)
    assert (
        call_confirms(
            "caution",
            "notify",
            "",
            {"text": "hi"},
            cwd=str(tmp_path),
            consequences=sends,
            own=True,
            tells_owner=True,
        )
        == ()
    )


def test_a_call_that_only_tells_the_owner_is_exempt_from_the_send_alone(tmp_path):
    assert call_confirms(
        "caution",
        "app_shop_notify",
        "",
        {"text": "hi"},
        cwd=str(tmp_path),
        consequences=(SENDS, SPENDS),
        tells_owner=True,
    ) == (SPENDS,)


def test_a_spend_asks(tmp_path):
    assert _confirms(
        tmp_path, "caution", "app_shop_buy", {"item": "x"}, consequences=(SPENDS,)
    ) == (SPENDS,)


def test_an_established_read_never_asks_whatever_its_tool_declares(tmp_path):
    assert _confirms(tmp_path, "safe", "mcp/srv/get", {}) == ()
    assert _confirms(tmp_path, "safe", "app_shop_list", {}, consequences=(SENDS, SPENDS)) == ()


# ── A tool that says nothing about what it does ──────────────────────────────────────────────────


def test_an_undeclared_mcp_tool_that_writes_asks_for_the_confirm(tmp_path):
    assert _confirms(tmp_path, "caution", "mcp/srv/create_page", {"title": "x"}) == (UNDECLARED,)


def test_an_undeclared_mcp_tool_that_only_reads_does_not(tmp_path):
    # A read the owner's trust in the server established (its readOnlyHint believed): SAFE.
    assert _confirms(tmp_path, "safe", "mcp/srv/list_pages", {}) == ()


def test_an_app_tool_that_declares_neither_decides_on_the_first_press(tmp_path):
    assert _confirms(tmp_path, "caution", "app_notes_save", {"x": 1}, consequences=()) == ()


def test_a_built_in_tool_is_classified_even_with_no_declaration(tmp_path):
    assert _confirms(tmp_path, "caution", "knowledge_create", {"title": "x"}, own=True) == ()


# ── An agent CLI's own tools ─────────────────────────────────────────────────────────────────────


def test_a_cli_shell_call_is_judged_by_its_command(tmp_path):
    inp = '{"command": "rm -rf build"}'
    assert _confirms(tmp_path, "", "Run rm -rf build", inp, kind="execute") == (DELETES,)
    inp = '{"command": "ls"}'
    assert _confirms(tmp_path, "", "Run ls", inp, kind="execute") == ()


def test_a_cli_edit_is_judged_by_the_files_it_names(tmp_path):
    inside = str(tmp_path / "notes.md")
    assert _confirms(tmp_path, "", "Edit notes.md", "{}", kind="edit", named_files=(inside,)) == ()
    assert _confirms(
        tmp_path, "", "Edit hosts", "{}", kind="edit", named_files=("/etc/hosts",)
    ) == (OUTSIDE_WORKSPACE,)
    # An edit that names no file could be anywhere.
    assert _confirms(tmp_path, "", "Edit", "{}", kind="edit") == (OUTSIDE_WORKSPACE,)


def test_any_other_cli_tool_counts_as_undeclared(tmp_path):
    assert _confirms(tmp_path, "", "mcp__srv__do", "{}", kind="other") == (UNDECLARED,)


# ── The declaration ──────────────────────────────────────────────────────────────────────────────


def test_a_definition_that_declares_nothing_is_none_and_one_that_declares_is_its_list():
    assert ToolDefinition(name="t", description="d").consequences is None
    assert consequences_of(None) is None
    assert consequences_of(["spends", "sends"]) == (SENDS, SPENDS)
    assert consequences_of([]) == ()
    # A word this reading does not know makes the whole declaration one it cannot follow.
    assert consequences_of(["spends", "bogus", "sends"]) is None


def test_notify_declares_that_it_sends():
    from personalclaw import mcp_core

    tool = mcp_core.own_tool("notify")
    assert tool is not None
    assert tool["_meta"][CONSEQUENCES_META_KEY] == [SENDS]


@pytest.mark.asyncio
async def test_the_in_process_provider_carries_the_declaration():
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    defs = {d.name: d for d in await InProcessMcpToolProvider().list_tools()}
    assert defs["notify"].consequences == (SENDS,)
    assert defs["skill_search"].consequences is None


# ── The row every surface reads ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_pending_row_carries_its_confirms(world, tmp_path):  # noqa: F811
    task = asyncio.create_task(
        world.state.request_approval(
            "bg:del1",
            "trigger",
            "bash",
            tool_input={"command": "rm -rf build"},
            risk_level="destructive",
            cwd=str(tmp_path),
        )
    )
    await _until(lambda: "bg:del1" in world.state._pending_approvals, "never listed")
    assert world.state._pending_approvals["bg:del1"]["confirms"] == [DELETES]
    world.state.resolve_approval("bg:del1", False, by=YOU)
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_a_background_call_to_an_undeclared_tool_asks_the_confirm(world):  # noqa: F811
    task = asyncio.create_task(
        world.state.request_approval(
            "bg:mcp1",
            "trigger",
            "mcp/srv/create_page",
            tool_input={"title": "x"},
            risk_level="caution",
        )
    )
    await _until(lambda: "bg:mcp1" in world.state._pending_approvals, "never listed")
    assert world.state._pending_approvals["bg:mcp1"]["confirms"] == [UNDECLARED]
    world.state.resolve_approval("bg:mcp1", False, by=YOU)
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_a_batchs_consent_is_personalclaws_own_ask_and_asks_no_second_confirmation(
    world,  # noqa: F811
):
    """A batch's one ask for consent is PersonalClaw's own, naming every task: it is classified,
    so it decides on the first press like every other built-in."""
    from personalclaw.workflows import owner_allow

    task = asyncio.create_task(
        owner_allow.ask(
            world.state,
            ask_id="batch:subagent-batch-1",
            source="subagent",
            tool="subagent_run",
            purpose="Starts 2 tasks at once",
            said="1. Raise the retry ceiling",
            session="",
        )
    )
    await _until(lambda: "batch:subagent-batch-1" in world.state._pending_approvals, "never listed")
    assert world.state._pending_approvals["batch:subagent-batch-1"]["confirms"] == []
    world.state.resolve_approval("batch:subagent-batch-1", False, by=YOU)
    await asyncio.wait_for(task, timeout=2)
