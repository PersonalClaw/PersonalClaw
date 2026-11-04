"""A project's secret file opens for a chat you allow, and for nothing else.

The file tools refuse every credential and secret file. A project's own secret file, by its name
and nothing more (``.env``, ``secrets.json``, a project's ``.netrc``…), is the one they may ask for:
``read_file`` to read it, ``write_file`` and ``edit_file`` to change it. The ask is the owner's
alone, whatever the chat's approval mode, and its Allow is for this session: that file, that
operation, that chat, until it ends. A Deny, or a turn nobody attends, keeps today's refusal.

Measured before this change, each 🔴 test below: every file tool refused ``.env`` with no ask in
every approval mode, so a chat could never read a project's own environment file even when its
owner wanted it to, and the shell's refusal named no way that could.

Each has its control: without the ask the refusal is what it was; a search and a listing leave the
file out after an Allow; PersonalClaw's own credential store, a sign-in file in the home and a file
in a credential folder are never offered; and the shell still refuses the file, an Allow or not.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import personalclaw.config.loader as loader
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.file_scope import FileScope, OutOfScope
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)

#: The file tools' refusal of a secret file, as it was before an ask could open one.
REFUSED = "is a credential or secret file, which this tool does not reach"
#: What the project's environment file holds: a setting, and a key the tools hand on masked.
NAME = "SHOP_NAME=Lantern Goods"
KEY = "SHOP_API_KEY=lantern4242"
ENV = f"{NAME}\n{KEY}\n"

#: The native runtime's approval policies: ask (the default), and the three that approve a call
#: without asking anyone (a chat's Trust or YOLO, a standing grant, accepting edits).
POLICIES = ["", "auto", "yolo", "acceptEdits"]

_SESSION = "dashboard:chat-shop"
_OTHER = "dashboard:chat-garden"


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """The owner's home folder, PersonalClaw's home in it, and a project of hers with its own
    environment file beside its code."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    project = user / "projects" / "shop"
    project.mkdir(parents=True)
    pc.mkdir(parents=True)
    (project / ".env").write_text(ENV)
    (project / "README.md").write_text("# Shop\n")
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    return SimpleNamespace(user=user, home=pc, project=project, env=project / ".env")


@pytest.fixture(autouse=True)
def _no_grant_outlives_its_test():
    yield
    grants = sys.modules.get("personalclaw.secret_file_grants")
    if grants is not None:
        grants.clear()


class _Model:
    """Replays scripted turns, one per inference."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


async def _turn(
    folder: Path,
    *calls: tuple[str, dict[str, Any]],
    policy: str = "",
    asks: bool = True,
    answer: str = "approve",
    unattended: bool = False,
    session: str = _SESSION,
) -> list[AgentEvent]:
    """One native turn in *folder* whose model makes *calls* in order, one per step, under
    approval *policy*, in a chat that asks for a project secret file when *asks*. An ask for one is
    answered *answer*; every other question is answered yes. Returns what it streamed."""
    steps = [
        [
            AgentEvent(
                kind=EVENT_TOOL_CALL, tool_call_id=f"c{n}", title=tool, tool_input=json.dumps(args)
            ),
            AgentEvent(kind=EVENT_COMPLETE),
        ]
        for n, (tool, args) in enumerate(calls, 1)
    ]
    done = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([*steps, done]),
        tool_providers=[NativeBuiltinToolProvider(folder, session_key=session)],
        cwd=folder,
        session_key=session,
        unattended=unattended,
    )
    runtime.set_approval_policy(policy)
    arm = getattr(runtime, "set_secret_file_asks", None)
    if arm is not None:
        arm(asks)
    await runtime.start()
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in runtime.stream("go"):
            seen.append(ev)
            if ev.kind != EVENT_PERMISSION_REQUEST:
                continue
            if getattr(ev, "secret_file", "") and answer != "approve":
                await runtime.reject_tool(ev.request_id)
            else:
                await runtime.approve_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    return seen


def _secret_asks(seen: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in seen if e.kind == EVENT_PERMISSION_REQUEST and getattr(e, "secret_file", "")]


def _results(seen: list[AgentEvent]) -> list[str]:
    return [str(e.tool_output) for e in seen if e.kind == EVENT_TOOL_RESULT]


def _scope(folder: Path) -> FileScope:
    return FileScope([folder])


# ── the refusal, unasked ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize(
    "call",
    [
        ("read_file", {"path": ".env"}),
        ("write_file", {"path": ".env", "content": "SHOP_API_KEY=changed\n"}),
        ("edit_file", {"path": ".env", "old_str": "4242", "new_str": "0000"}),
    ],
    ids=["read", "write", "edit"],
)
async def test_a_chat_that_does_not_ask_still_refuses_a_project_secret_file(owner, policy, call):
    seen = await _turn(owner.project, call, policy=policy, asks=False)

    assert _secret_asks(seen) == []
    assert REFUSED in _results(seen)[-1]
    assert owner.env.read_text() == ENV


# ── Allow for this session ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", POLICIES)
async def test_an_allow_reads_that_file_for_the_rest_of_the_chat_whatever_its_mode(owner, policy):
    """🔴 Every mode refused ``.env`` and asked nobody. Now the owner is asked, YOLO and Trust
    included, and after the Allow the call runs again and the next read does not ask. The file's
    values reach the chat masked, as every tool result's do."""
    read = ("read_file", {"path": ".env"})

    seen = await _turn(owner.project, read, read, policy=policy)

    asks = _secret_asks(seen)
    assert [a.secret_file for a in asks] == ["read"], "asked once, to read it"
    assert asks[0].title == "read_file"
    assert all(NAME in r for r in _results(seen)), _results(seen)
    assert not any("lantern4242" in r for r in _results(seen))


@pytest.mark.asyncio
async def test_reading_it_does_not_let_the_chat_change_it(owner):
    """🔴 An Allow to read is not one to change: a change asks again, and its Deny keeps the file."""
    added = f"{NAME}\nSHOP_REGION=eu"
    edit = ("edit_file", {"path": ".env", "old_str": NAME, "new_str": added})

    seen = await _turn(owner.project, ("read_file", {"path": ".env"}), edit)

    assert [a.secret_file for a in _secret_asks(seen)] == ["read", "write"]
    assert owner.env.read_text() == f"{added}\n{KEY}\n", _results(seen)

    owner.env.write_text(ENV)
    denied = await _turn(
        owner.project, ("read_file", {"path": ".env"}), edit, answer="reject", session=_OTHER
    )
    assert [a.secret_file for a in _secret_asks(denied)] == ["read", "write"]
    assert owner.env.read_text() == ENV


@pytest.mark.asyncio
async def test_a_deny_or_a_turn_nobody_attends_keeps_the_refusal_and_allows_nothing(owner):
    from personalclaw import secret_file_grants

    read = ("read_file", {"path": ".env"})
    denied = await _turn(owner.project, read, read, answer="reject")
    assert [a.secret_file for a in _secret_asks(denied)] == ["read", "read"], "a Deny keeps nothing"
    assert all(REFUSED in r for r in _results(denied))
    assert not secret_file_grants.holds(_SESSION, os.path.realpath(owner.env), "read")

    unattended = await _turn(owner.project, read, unattended=True)
    assert _secret_asks(unattended) == []
    assert REFUSED in _results(unattended)[-1]


def test_an_allow_is_one_chats_and_ends_with_it(owner):
    """🔴 The grant is the file, the operation and the chat: another chat, another file and the
    other operation are refused, and it is gone once the chat is deleted or the gateway restarts."""
    from personalclaw import secret_file_grants

    real = os.path.realpath(owner.env)
    (owner.project / "prod.env").write_text("DB=postgres://prod\n")
    scope = _scope(owner.project)

    def reaches(session: str, tool: str, path: str = ".env") -> bool:
        meta: dict = {}
        try:
            with secret_file_grants.call(session, tool, meta):
                scope.resolve(path, change=tool in ("write_file", "edit_file"))
        except OutOfScope as refused:
            assert REFUSED in str(refused), "refused in the words it always was"
            return False
        return True

    assert secret_file_grants.allow(_SESSION, real, secret_file_grants.READ)
    assert reaches(_SESSION, "read_file")
    assert not reaches(_SESSION, "write_file")
    assert not reaches(_SESSION, "read_file", "prod.env")
    assert not reaches(_OTHER, "read_file")
    assert not reaches(_SESSION, "grep"), "a search never reaches it"
    assert not reaches(_SESSION, "list_dir"), "nor a listing"

    assert secret_file_grants.end(_SESSION) == 1
    assert not reaches(_SESSION, "read_file")

    secret_file_grants.allow(_SESSION, real, secret_file_grants.READ)
    secret_file_grants.clear()
    assert not reaches(_SESSION, "read_file")


def test_a_refused_call_says_what_it_may_ask_for_and_a_grant_says_what_it_reached(owner):
    from personalclaw import secret_file_grants

    real = os.path.realpath(owner.env)
    scope = _scope(owner.project)
    meta: dict = {}
    with pytest.raises(OutOfScope), secret_file_grants.call(_SESSION, "edit_file", meta):
        scope.resolve(".env", change=True)
    assert meta == {secret_file_grants.ASKS_META_KEY: {"path": real, "op": "write"}}

    secret_file_grants.allow(_SESSION, real, secret_file_grants.WRITE)
    meta = {}
    with secret_file_grants.call(_SESSION, "edit_file", meta):
        scope.resolve(".env", change=True)
    assert meta == {secret_file_grants.USED_META_KEY: {"path": real, "op": "write"}}

    meta = {}
    with secret_file_grants.call(_SESSION, "edit_file", meta, uses=False):
        scope.resolve(".env", change=True)
    assert meta == {}, "a pre-flight check reaches nothing"


@pytest.mark.asyncio
async def test_a_subagent_of_a_chat_that_was_allowed_the_file_is_refused_it_and_asks_no_one(owner):
    """An Allow is its chat's. The chat's subagent runs under a key of its own
    (``session_keys.SUBAGENT``) and its host never asks for a secret file, so with YOLO on and the
    chat allowed to read ``.env`` the subagent gets the refusal, and nobody is asked."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw import secret_file_grants
    from personalclaw.subagent import SubagentManager

    real = os.path.realpath(owner.env)
    assert secret_file_grants.allow(_SESSION, real, secret_file_grants.READ)
    read = AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id="c1", title="read_file", tool_input='{"path": ".env"}'
    )
    model = _Model(
        [
            [read, AgentEvent(kind=EVENT_COMPLETE)],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[NativeBuiltinToolProvider(owner.project)],
        cwd=owner.project,
    )
    await runtime.start()
    keys: list[str] = []

    async def _runtime_for(key: str, *_a: Any, **_kw: Any):
        keys.append(key)
        runtime.set_session_key(key)
        return runtime, True, False

    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(side_effect=_runtime_for)
    asked = AsyncMock(return_value=True)
    manager = SubagentManager(
        sessions=sessions,
        ctx_builder=_mock_ctx_builder(),
        on_tool_approval=asked,
        is_yolo=lambda: True,
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("Read the shop's settings.", parent_session_key=_SESSION)
        assert info is not None
        await asyncio.wait_for(manager._tasks[info.id], timeout=20)

    assert keys and _SESSION not in keys, keys
    asked.assert_not_awaited()
    told = [str(m.get("content")) for m in runtime._messages if m.get("role") == "tool"]
    assert told and REFUSED in told[-1] and NAME not in told[-1], told
    assert secret_file_grants.holds(_SESSION, real, "read"), "the chat's own Allow stands"


# ── what may be allowed ──────────────────────────────────────────────────────────────────────────


def test_only_a_project_secret_file_by_its_name_can_be_allowed(owner):
    """A project's own secret files may be allowed. PersonalClaw's credential store, a sign-in
    file in the home, a file in a credential folder, key material and a second name of a file
    never are, nor is a template, which is no secret."""
    from personalclaw import secret_file_grants

    project = owner.project
    for name in (".env.local", "secrets.json", "service-account.json", ".netrc", ".npmrc"):
        (project / name).write_text("x\n")
    (owner.home / ".env").write_text("OPENAI_API_KEY=own-store-value\n")
    (owner.user / ".netrc").write_text("machine h login me password p\n")
    (owner.user / ".aws").mkdir()
    (owner.user / ".aws" / "credentials.json").write_text("{}\n")
    (project / ".env.example").write_text("SHOP_API_KEY=\n")
    (project / ".env.pem").write_text("-----BEGIN-----\n")
    os.link(project / ".env.local", project / ".env.staging")

    allowed = [".env", "secrets.json", "service-account.json", ".netrc", ".npmrc"]
    for name in allowed:
        assert secret_file_grants.grantable(os.path.realpath(project / name)), name
    never = [
        owner.home / ".env",
        owner.user / ".netrc",
        owner.user / ".aws" / "credentials.json",
        project / ".env.example",
        project / ".env.pem",
        project / ".env.staging",
        project / "README.md",
    ]
    for path in never:
        real = os.path.realpath(path)
        assert not secret_file_grants.grantable(real), path
        assert not secret_file_grants.allow(_SESSION, real, secret_file_grants.READ), path


# ── what an Allow does not open ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_search_and_a_listing_still_leave_it_out_after_an_allow(owner):
    seen = await _turn(
        owner.project,
        ("read_file", {"path": ".env"}),
        ("grep", {"pattern": "Lantern"}),
        ("list_dir", {"path": "."}),
    )
    read, searched, listed = _results(seen)
    assert NAME in read
    assert "Lantern" not in searched
    assert ".env" not in listed.split() and "README.md" in listed


@pytest.mark.asyncio
async def test_the_shell_still_refuses_it_and_names_the_file_tools_that_ask(owner):
    seen = await _turn(
        owner.project, ("read_file", {"path": ".env"}), ("bash", {"command": "cat .env"})
    )
    shell = [e for e in seen if e.kind == EVENT_TOOL_RESULT][-1]
    assert "lantern4242" not in str(shell.tool_output)
    hints = " ".join(shell.tool_meta.get("recovery_hints") or [])
    assert "read_file" in hints and "write_file" in hints and "edit_file" in hints


# ── the record ───────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_allow_and_each_use_are_in_the_security_log_without_the_value(owner, monkeypatch):
    log = MagicMock()
    monkeypatch.setattr("personalclaw.sel.sel", lambda: log)
    read = ("read_file", {"path": ".env"})

    await _turn(owner.project, read, read)

    real = os.path.realpath(owner.env)
    rows = [
        c.kwargs
        for c in log.log_api_access.call_args_list
        if str(c.kwargs.get("operation", "")).startswith("secret_file_")
    ]
    assert [(r["operation"], r["outcome"]) for r in rows] == [
        ("secret_file_granted", "granted"),
        ("secret_file_grant_used", "allowed"),
        ("secret_file_grant_used", "allowed"),
    ]
    for row in rows:
        assert row["resources"] == f"path={real} op=read"
        assert row["metadata"]["session"] == _SESSION
        assert "lantern4242" not in json.dumps(row)


@pytest.mark.asyncio
async def test_an_allow_the_log_cannot_record_is_not_kept_and_a_use_it_cannot_record_stands(
    owner, monkeypatch
):
    from personalclaw import secret_file_grants

    def refusing(operation: str) -> MagicMock:
        log = MagicMock()

        def write(**row: Any) -> None:
            if row.get("operation") == operation:
                raise OSError("the log's file cannot be written")

        log.log_api_access.side_effect = write
        return log

    read = ("read_file", {"path": ".env"})
    real = os.path.realpath(owner.env)
    no_grant = refusing("secret_file_granted")
    monkeypatch.setattr("personalclaw.sel.sel", lambda: no_grant)
    seen = await _turn(owner.project, read)
    assert REFUSED in _results(seen)[-1]
    assert not secret_file_grants.holds(_SESSION, real, "read")

    no_use = refusing("secret_file_grant_used")
    monkeypatch.setattr("personalclaw.sel.sel", lambda: no_use)
    seen = await _turn(owner.project, read)
    assert NAME in _results(seen)[-1]
