"""``personalclaw chat`` opens on the folder, agent, project and named chat its flags name.

It took only ``-m``, ``--model`` and ``--port``, and posted only the model: every terminal chat
opened as a bare New chat, on the default agent and workspace, in no project, and a new chat each
time. So a chat could not work on a repository with its owner there to answer its calls, and
``run``, which has ``--cwd``, ``--agent`` and ``--session``, asks nobody. Now ``--agent`` and
``--project`` go in the create body, ``--session`` names an attended chat to go on with, and
``--cwd`` goes through the chat's folder route after it opens and before its first message.

The command is typed against a gateway on this machine's loopback (``test_cli_chat._gateway``):
the real create, folder, project-list, chat-list and turn routes behind the real token middleware.
What is read is what the terminal printed, what the gateway was sent in what order, and the chat it
holds.

🔴 Red before: every row but the one that a chat with no new flag opens as it did.
"""

from __future__ import annotations

import builtins
import contextlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_cli_chat import _gateway, _says, _type
from test_native_runtime import _ScriptedModel

from personalclaw.config import loader as config_loader
from personalclaw.guardrails.policy import is_unattended_session
from personalclaw.tasks.hierarchy import HierarchyStore


def _recorded(seen: list[tuple[str, str, Any]]):
    """The extra routes, and a record of each request the gateway is sent: (method, path, body)."""
    from aiohttp import web

    from personalclaw.dashboard.chat_handlers import (
        api_chat_session_workspace_dir,
        api_chat_sessions,
    )
    from personalclaw.tasks.hierarchy_handlers import api_projects_list

    @web.middleware
    async def record(request: web.Request, handler):
        body = None
        if request.method == "POST" and request.can_read_body:
            with contextlib.suppress(ValueError):
                body = json.loads(await request.read() or b"null")
        seen.append((request.method, request.path, body))
        return await handler(request)

    def extra(app: web.Application) -> None:
        app.middlewares.append(record)
        app.router.add_get("/api/projects", api_projects_list)
        app.router.add_get("/api/chat/sessions", api_chat_sessions)
        app.router.add_post(
            "/api/chat/sessions/{session}/workspace-dir", api_chat_session_workspace_dir
        )

    return extra


def _sent(seen: list[tuple[str, str, Any]]) -> list[str]:
    """The paths posted, in order: what the command asked the gateway to do."""
    return [path for method, path, _ in seen if method == "POST"]


def _body(seen: list[tuple[str, str, Any]], path: str) -> Any:
    (body,) = [b for method, p, b in seen if method == "POST" and p == path]
    return body


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository folder the owner names, as ``--cwd`` takes it."""
    folder = tmp_path / "site"
    folder.mkdir()
    return folder


# ── The folder and the agent ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_folder_is_set_after_the_chat_opens_and_before_its_message(
    tmp_path, monkeypatch, capsys, repo
):
    seen: list = []
    model = _ScriptedModel([_says("Read it.")])
    async with _gateway(tmp_path, model, monkeypatch, _recorded(seen)) as gw:
        code = await _type("--cwd", str(repo), "-m", "hi", "--port", str(gw.port))
        chat = gw.chat()

    assert code == 0, capsys.readouterr().err
    folder = f"/api/chat/sessions/{chat.key}/workspace-dir"
    assert _sent(seen) == ["/api/chat/sessions", folder, "/api/chat"]
    assert _body(seen, folder) == {"workspace_dir": str(repo)}
    assert chat.workspace_dir == os.path.realpath(repo)


@pytest.mark.asyncio
async def test_a_folder_is_named_from_the_shells_folder(tmp_path, monkeypatch, capsys, repo):
    """``--cwd .`` is the folder the command was typed in: the gateway is sent where that is."""
    seen: list = []
    monkeypatch.chdir(repo)
    async with _gateway(
        tmp_path, _ScriptedModel([_says("ok")]), monkeypatch, _recorded(seen)
    ) as gw:
        code = await _type("--cwd", ".", "-m", "hi", "--port", str(gw.port))
        chat = gw.chat()

    assert code == 0, capsys.readouterr().err
    assert _body(seen, f"/api/chat/sessions/{chat.key}/workspace-dir") == {
        "workspace_dir": str(repo)
    }


@pytest.mark.asyncio
async def test_the_agent_goes_in_the_create_body(tmp_path, monkeypatch, capsys):
    seen: list = []
    async with _gateway(
        tmp_path, _ScriptedModel([_says("ok")]), monkeypatch, _recorded(seen)
    ) as gw:
        code = await _type("--agent", "coder", "-m", "hi", "--port", str(gw.port))
        chat = gw.chat()

    assert code == 0, capsys.readouterr().err
    assert _body(seen, "/api/chat/sessions") == {"agent": "coder"}
    assert chat.agent == "coder"


@pytest.mark.asyncio
async def test_a_chat_with_no_new_flag_opens_as_it_did(tmp_path, monkeypatch, capsys):
    """The default agent and workspace, no project, a new chat: the create body is empty, and the
    folder and project routes are not asked."""
    seen: list = []
    async with _gateway(
        tmp_path, _ScriptedModel([_says("ok")]), monkeypatch, _recorded(seen)
    ) as gw:
        code = await _type("-m", "hi", "--port", str(gw.port))

    assert code == 0, capsys.readouterr().err
    assert _sent(seen) == ["/api/chat/sessions", "/api/chat"]
    assert _body(seen, "/api/chat/sessions") == {}
    assert not [path for _, path, _ in seen if path == "/api/projects"]


@pytest.mark.asyncio
async def test_the_dashboard_lists_it_with_its_folder_and_agent(
    tmp_path, monkeypatch, capsys, repo
):
    async with _gateway(tmp_path, _ScriptedModel([_says("ok")]), monkeypatch, _recorded([])) as gw:
        code = await _type(
            "--cwd", str(repo), "--agent", "coder", "-m", "hi", "--port", str(gw.port)
        )
        key = gw.chat().key
        status, rows = await gw.owner("GET", "/api/chat/sessions")

    assert (code, status) == (0, 200), capsys.readouterr().err
    (row,) = [r for r in rows if r["key"] == key]
    assert (row["workspace_dir"], row["agent"]) == (os.path.realpath(repo), "coder")


# ── A folder the gateway refuses ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "folder, reason",
    [
        (lambda tmp: tmp / "gone", "Not a directory"),
        (lambda tmp: config_loader.config_dir() / "credentials", "Access denied"),
    ],
    ids=["not a folder", "the home's credentials folder"],
)
async def test_a_refused_folder_ends_the_command_with_its_reason_and_no_message_sent(
    tmp_path, monkeypatch, capsys, folder, reason
):
    named = folder(tmp_path)
    if named.name == "credentials":
        named.mkdir(parents=True, exist_ok=True)
    seen: list = []
    model = _ScriptedModel([_says("never")])
    async with _gateway(tmp_path, model, monkeypatch, _recorded(seen)) as gw:
        code = await _type("--cwd", str(named), "-m", "hi", "--port", str(gw.port))
        chat = gw.chat()

    out, err = capsys.readouterr()
    assert code == 1, err
    assert (
        f"personalclaw chat: The chat can't work in {named}: {reason}. No message was sent." in err
    )
    assert "/api/chat" not in _sent(seen)
    assert (model.calls, chat.messages, out) == (0, [], "")


@pytest.mark.asyncio
async def test_a_refused_folder_ends_the_interactive_chat_too(tmp_path, monkeypatch, capsys):
    """Its first message opens the chat; the folder refused, the chat ends there rather than
    asking for the next line."""
    typed = iter(["hi", "and this"])

    def answer(_prompt: str = "") -> str:
        return next(typed)

    monkeypatch.setattr(builtins, "input", answer)
    seen: list = []
    model = _ScriptedModel([_says("never")])
    async with _gateway(tmp_path, model, monkeypatch, _recorded(seen)) as gw:
        code = await _type("--cwd", str(tmp_path / "gone"), "--port", str(gw.port))

    assert code == 1, capsys.readouterr().err
    assert "/api/chat" not in _sent(seen)
    assert _sent(seen).count("/api/chat/sessions") == 1
    assert next(typed) == "and this", "the chat asked for another line"


# ── The project ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("by", ["name", "id"])
async def test_the_chat_opens_in_the_project_named_by_its_name_or_its_id(
    tmp_path, monkeypatch, capsys, by
):
    seen: list = []
    async with _gateway(
        tmp_path, _ScriptedModel([_says("ok")]), monkeypatch, _recorded(seen)
    ) as gw:
        site = HierarchyStore().create_project("Website")
        HierarchyStore().create_project("Garden")
        named = site.name if by == "name" else site.id
        code = await _type("--project", named, "-m", "hi", "--port", str(gw.port))
        chat = gw.chat()

    assert code == 0, capsys.readouterr().err
    assert _body(seen, "/api/chat/sessions") == {"project_id": site.id}
    assert chat.project_id == site.id


@pytest.mark.asyncio
async def test_a_name_two_projects_share_is_refused_with_both_ids(tmp_path, monkeypatch, capsys):
    """Two projects whose names differ only in case share ``website``: neither is guessed, and no
    chat is opened."""
    seen: list = []
    model = _ScriptedModel([_says("never")])
    async with _gateway(tmp_path, model, monkeypatch, _recorded(seen)) as gw:
        ours = HierarchyStore().create_project("Website")
        theirs = HierarchyStore().create_project("website")
        code = await _type("--project", "WEBSITE", "-m", "hi", "--port", str(gw.port))
        assert not gw.state._sessions

    err = capsys.readouterr().err
    assert code == 1, err
    assert ours.id in err and theirs.id in err, err
    assert "--project ID" in err
    assert _sent(seen) == [] and model.calls == 0


@pytest.mark.asyncio
async def test_a_project_no_name_or_id_finds_is_refused(tmp_path, monkeypatch, capsys):
    seen: list = []
    async with _gateway(tmp_path, _ScriptedModel([]), monkeypatch, _recorded(seen)) as gw:
        code = await _type("--project", "Website", "-m", "hi", "--port", str(gw.port))

    assert code == 1
    assert "No project is named 'Website' or has that id." in capsys.readouterr().err
    assert _sent(seen) == []


# ── The named chat ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_named_chat_is_opened_again_on_a_second_run_and_is_attended(
    tmp_path, monkeypatch, capsys
):
    model = _ScriptedModel([_says("one"), _says("two")])
    async with _gateway(tmp_path, model, monkeypatch, _recorded([])) as gw:
        first = await _type("--session", "notes", "-m", "first", "--port", str(gw.port))
        second = await _type("--session", "notes", "-m", "second", "--port", str(gw.port))
        chat = gw.chat()

    assert (first, second) == (0, 0), capsys.readouterr().err
    assert chat.key == "notes"
    assert not is_unattended_session(chat.key), chat.key
    assert [m["content"] for m in chat.messages if m["role"] == "user"] == ["first", "second"]


@pytest.mark.parametrize("name", ["inbound:cli:notes", "..", "my notes", "loop:abc"])
def test_a_name_that_is_not_a_chats_own_is_refused_before_the_gateway_is_asked(capsys, name):
    """``run``'s ``inbound:cli:`` form, another kind's, a name that would be a path of its own:
    refused as a usage error, before any gateway is looked for (nothing listens on port 1)."""
    from personalclaw import cli, cli_chat

    with pytest.raises(SystemExit) as exited:
        cli_chat._chat(
            cli.build_parser().parse_args(["chat", "--session", name, "-m", "hi", "--port", "1"])
        )
    assert exited.value.code == 2
    assert "a chat's name holds only letters, digits" in capsys.readouterr().err
