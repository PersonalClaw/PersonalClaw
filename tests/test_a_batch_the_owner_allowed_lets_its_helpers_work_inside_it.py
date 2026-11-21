"""A batch the owner allowed lets its helpers read and edit inside their folders without asking.

The batch's ask names each task, what it may change and where its tasks work, and her Allow started
them. Each task's calls then asked as any agent's do: one task of a four-task batch raised 77 cards
in 30 minutes, ``git status`` and ``git diff`` in its own folder among them, marked safe and
read-only on the cards themselves.

Now her Allow also answers a task's call inside what the ask showed (`batch_task_scope`): a read
that names nothing outside the task's folder, and an edit of files inside it. A command that may
write, an edit outside the folder, a delete, a batch a grant started rather than her answer, and an
app's batch still ask.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import approval_grants
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

RUN = "workflow:run-batch-1"


def _shell(command: str) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=command,
        tool_kind="execute",
        request_id="req-1",
        tool_input=json.dumps({"command": command}),
    )


def _edit(path: str, *, kind: str = "edit", title: str = "Edit file") -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=title,
        tool_kind=kind,
        request_id="req-1",
        tool_input=json.dumps({"file_path": path, "content": "x"}),
    )


@pytest.fixture()
def folder(tmp_path):
    task = tmp_path / "task-1"
    task.mkdir()
    (task / "notes.md").write_text("n\n", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    return task


# ── what the Allow covers ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda f: _shell("git status"),
        lambda f: _shell("git diff"),
        lambda f: _shell("cat notes.md"),
        lambda f: _edit(str(f / "notes.md")),
        lambda f: _edit("notes.md"),
        lambda f: _edit(str(f / "new" / "file.py")),
        lambda f: _edit(str(f / "notes.md"), kind="", title="write_file"),
        lambda f: _edit(str(f / "notes.md"), kind="read", title="Read notes.md"),
    ],
    ids=[
        "git status",
        "git diff",
        "a read-only command naming a file inside",
        "an edit inside",
        "an edit by a relative path",
        "a new file inside",
        "PersonalClaw's own write_file inside",
        "a file read inside",
    ],
)
def test_a_read_or_an_edit_inside_the_tasks_folder_is_covered(folder, call):
    from personalclaw.batch_task_scope import covers

    assert covers(call(folder), str(folder)) is True


@pytest.mark.parametrize(
    "call",
    [
        lambda f: _shell("cat /etc/hosts"),
        lambda f: _shell("cat ../elsewhere/x"),
        lambda f: _shell("touch made.txt"),
        lambda f: _shell("git commit -m done"),
        lambda f: _shell("curl https://example.com"),
        lambda f: _edit(str(f.parent / "elsewhere" / "notes.md")),
        lambda f: _edit(str(f / ".." / "elsewhere" / "notes.md")),
        lambda f: _edit(str(f / "notes.md"), kind="delete", title="Delete notes.md"),
        lambda f: _edit(str(f / "notes.md"), kind="other", title="frobnicate"),
        lambda f: LLMEvent(kind=EVENT_PERMISSION_REQUEST, title="Edit", tool_kind="edit"),
    ],
    ids=[
        "a read outside",
        "a read out by ..",
        "a command that writes",
        "a commit",
        "a network call",
        "an edit outside",
        "an edit out by ..",
        "a delete",
        "a tool that is neither a read nor an edit",
        "an edit naming no file",
    ],
)
def test_anything_else_still_asks(folder, call):
    from personalclaw.batch_task_scope import covers

    assert covers(call(folder), str(folder)) is False


def test_a_link_out_of_the_folder_is_out(folder):
    from personalclaw.batch_task_scope import covers

    os.symlink(folder.parent / "elsewhere", folder / "out")

    assert covers(_edit(str(folder / "out" / "x.md")), str(folder)) is False


def test_a_task_with_no_folder_and_a_secret_file_are_never_covered(folder):
    from personalclaw.batch_task_scope import covers

    secret = _edit(str(folder / ".env"))
    secret.secret_file = "write"

    assert covers(_shell("git status"), "") is False
    assert covers(secret, str(folder)) is False


# ── a batch helper's calls, through its permission loop ──────────────────────


def _only_from_the_grants(run):
    """``store.get`` answering *run* for :data:`RUN` to `approval_grants` alone, which reads a
    batch's Allow; to anything else, no run."""
    import inspect

    def _get(run_id):
        caller = inspect.stack()[1].frame.f_globals.get("__name__", "")
        return (
            run
            if run_id == RUN.split(":", 1)[1] and caller == "personalclaw.approval_grants"
            else None
        )

    return _get


async def _helper_asks(request: LLMEvent, *, cwd: str, by: str = approval_grants.YOU, app=""):
    """One turn of a batch task's agent CLI whose call asks; nothing but the batch's Allow stands.
    Returns the client and the relay every call that asks reaches (it answers no)."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    import personalclaw.config.loader as loader
    from personalclaw.hooks import ToolHookResult
    from personalclaw.subagent import SubagentInfo, SubagentManager

    (loader.config_dir() / "config.json").write_text(
        json.dumps({"agent": {"approval_mode": "interactive"}}), encoding="utf-8"
    )
    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    client = sessions.get_or_create.return_value[0]
    client.workspace = cwd

    async def _stream(*_a, **_kw):
        yield request
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    ctx = _mock_ctx_builder()
    ctx.hooks.on_tool_call = MagicMock(return_value=ToolHookResult.allow())
    ctx.hooks.auto_approve_subagent_tools = False
    relayed = AsyncMock(return_value=False)
    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False, on_tool_approval=relayed
    )
    info = SubagentInfo(
        id="bt0001", task="fix task 1", parent_session_key="", parent_run=RUN, cwd=cwd, app=app
    )
    from types import SimpleNamespace

    from personalclaw.workflows.batch_start import CONSENT_KEY
    from personalclaw.workflows.models import OriginKind

    # The batch's run, as `approval_grants` reads its Allow (any other reader of a run finds none).
    run = SimpleNamespace(
        origin=SimpleNamespace(kind=OriginKind.SUBAGENT_TOOL),
        extra={CONSENT_KEY: {"by": by}},
        project_id="",
    )
    with (
        patch("personalclaw.workflows.store.get", _only_from_the_grants(run)),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        await manager._run_inner(info, "subagent:bt0001")
    return client, relayed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [lambda f: _shell("git status"), lambda f: _edit(str(f / "notes.md"))],
    ids=["git status", "an edit inside"],
)
async def test_after_her_one_allow_a_task_reads_and_edits_inside_its_folder_unasked(folder, call):
    """🔴 Red before: each of these was relayed to the owner, as every call of a batch task was."""
    client, relayed = await _helper_asks(call(folder), cwd=str(folder))

    relayed.assert_not_awaited()
    client.approve_tool.assert_awaited_once_with("req-1")


@pytest.mark.asyncio
async def test_an_edit_outside_its_folder_still_asks(folder):
    client, relayed = await _helper_asks(
        _edit(str(folder.parent / "elsewhere" / "notes.md")), cwd=str(folder)
    )

    relayed.assert_awaited_once()
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("by", "app"),
    [(approval_grants.TRUST, ""), (approval_grants.YOU, "some-app")],
    ids=["a batch a grant started", "an app's batch"],
)
async def test_a_batch_she_did_not_answer_herself_still_asks_for_each_call(folder, by, app):
    client, relayed = await _helper_asks(_shell("git status"), cwd=str(folder), by=by, app=app)

    relayed.assert_awaited_once()
    client.approve_tool.assert_not_awaited()


# ── what her Allow never reaches, inside the folder too ──────────────────────────────────────────


@pytest.fixture()
def workspace():
    """The workspace in this test's home, a task's folder at its root, and a file of the task's."""
    from personalclaw.config.loader import memory_root

    root = memory_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.md").write_text("n\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "inside",
    [
        "_ext/chat-1/memory_index.db",
        "_ext/chat-1/learning.db",
        "knowledge/knowledge.db",
        "knowledge/files/report.md",
        "lexicon/lexicon.db",
    ],
    ids=[
        "a memory database",
        "a learning log",
        "the knowledge database",
        "a knowledge file",
        "the lexicon",
    ],
)
@pytest.mark.parametrize("kind", ["edit", "read"])
def test_a_task_at_the_workspace_root_still_asks_for_personalclaws_own_stores(
    workspace, inside, kind
):
    """🔴 Red before: its folder held them, so her Allow answered an agent CLI's own read or edit
    of PersonalClaw's memory, learning log or knowledge, which no file tool of PersonalClaw's
    reaches."""
    from personalclaw.batch_task_scope import covers

    target = workspace / inside
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")

    assert covers(_edit(str(target), kind=kind, title=f"{kind} it"), str(workspace)) is False
    assert covers(_shell(f"cat {inside}"), str(workspace)) is False
    assert covers(_edit(str(workspace / "notes.md"), kind=kind), str(workspace)) is True


def test_an_edit_of_gits_own_settings_inside_the_folder_still_asks(folder):
    from personalclaw.batch_task_scope import covers

    (folder / ".git" / "hooks").mkdir(parents=True)

    assert covers(_edit(str(folder / ".git" / "hooks" / "pre-commit")), str(folder)) is False
    assert covers(_edit(str(folder / ".git" / "config")), str(folder)) is False


@pytest.mark.parametrize(
    "tool_input",
    [
        lambda f: {"file_path": str(f / "notes.md"), "paths": [str(f.parent / "elsewhere" / "x")]},
        lambda f: {"file_path": str(f / "notes.md"), "content": "x", "target": "/etc/hosts"},
    ],
    ids=["a list of paths under a key the reader does not know", "a path under another key"],
)
@pytest.mark.parametrize("kind", ["edit", "read"])
def test_a_call_whose_input_holds_what_the_reader_does_not_know_asks(folder, tool_input, kind):
    """🔴 Red before: one file inside under a key the reader knows covered the whole call."""
    from personalclaw.batch_task_scope import covers

    call = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="Edit file",
        tool_kind=kind,
        request_id="req-1",
        tool_input=json.dumps(tool_input(folder)),
    )

    assert covers(call, str(folder)) is False


def test_a_command_given_a_folder_of_its_own_to_run_in_asks(folder):
    from personalclaw.batch_task_scope import covers

    call = LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title="git status",
        tool_kind="execute",
        request_id="req-1",
        tool_input=json.dumps({"command": "git status", "cwd": str(folder.parent / "elsewhere")}),
    )

    assert covers(call, str(folder)) is False
    assert covers(_shell("git status"), str(folder)) is True


@pytest.mark.asyncio
async def test_an_operator_ceiling_that_refuses_the_grant_has_the_task_ask(folder):
    client, relayed = await _helper_asks(_shell("git status"), cwd=str(folder))
    relayed.assert_not_awaited()

    with patch("personalclaw.guardrails.policy.ceiling_permits_approval", return_value=False):
        client, relayed = await _helper_asks(_shell("git status"), cwd=str(folder))

    relayed.assert_awaited_once()
    client.approve_tool.assert_not_awaited()
