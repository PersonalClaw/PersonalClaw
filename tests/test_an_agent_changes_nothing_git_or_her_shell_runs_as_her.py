"""An agent changes nothing that git or the owner's shell runs as her, whatever would approve it.

A repository's git settings and hook scripts (``.git/config``, ``.git/hooks/``, ``.gitmodules``
and the rest ``owner_only`` lists), the owner's own git settings (``~/.gitconfig``) and her shells'
startup files (``~/.zshrc``, ``~/.bashrc``, ``~/.profile`` …) are read, and what they name is run,
by her own git and her own shell, outside any sandbox. They are owner-only, as PersonalClaw's own
files in its home are: every path an agent writes by refuses a change to one before anyone is
asked, in the words the home's refusal uses, and no approval mode, Trust, YOLO or standing grant
turns that into a question.

Measured before this change, each 🔴 test below: the native ``write_file`` wrote a repository's
``.git/hooks/pre-commit`` and ``.git/config`` in the workspace under every approval mode; an agent
CLI's own write, edit and patch to them were approved by the chat's Trust; the agent's shell wrote
them, and set ``core.hooksPath`` with ``git config``; and a file-backed artifact could point at
``.git/config``, so each save of it wrote the file.

Each refusal has its control: the repository's ordinary files, ``.gitignore`` and
``.gitattributes`` among them, are written as before, reading these files is untouched, and so is
running a startup file in the agent's own shell (``source ~/.zshrc``).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import personalclaw.config.loader as loader
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    TOOL_META_NOT_RUN,
    AgentEvent,
    unasked_outcome,
    unasked_reason,
)

#: What a refusal of a change to one of git's files says, as the home's refusal says it.
GITS = "is where git keeps what it reads and runs as the owner, and an agent may not change it."
#: And of one of the owner's shell startup files.
SHELLS = "is where the owner's shell keeps what it runs as the owner each time it starts"
#: How each refusal ends: the owner edits it herself.
OWNERS = "Only the owner changes it, outside the chat."

SETTINGS = "[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
STARTUP = "export EDITOR=vi\n"

#: The native runtime's approval policies: ask (the default), and the three that approve a call
#: without asking anyone (a chat's Trust or YOLO, a standing grant, accepting edits).
POLICIES = ["", "auto", "yolo", "acceptEdits"]

_SESSION = "dashboard:chat-repo-settings"


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """The owner's home folder, PersonalClaw's home in it with a workspace, and in the workspace
    a repository laid out as git lays one out (no git is run to make it)."""
    user = tmp_path / "user"
    pc = user / ".personalclaw"
    workspace = pc / "workspace"
    repo = workspace / "notes-site"
    git = repo / ".git"
    for folder in (git / "hooks", git / "objects", git / "refs" / "heads", git / "info"):
        folder.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "config").write_text(SETTINGS)
    (git / "hooks" / "pre-commit.sample").write_text("#!/bin/sh\nexit 0\n")
    (git / "info" / "exclude").write_text("# ignored here only\n")
    (repo / "README.md").write_text("# Notes\n")
    (user / ".zshrc").write_text(STARTUP)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc))
    monkeypatch.setattr(loader, "config_dir", lambda: pc)
    return SimpleNamespace(user=user, home=pc, workspace=workspace, repo=repo, git=git)


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


async def _native_turn(folder: Path, *calls: tuple[str, dict[str, Any]], policy: str):
    """One native turn in *folder* whose model makes *calls* in order, one per step, under
    approval *policy*; every question is answered yes. Returns what it streamed."""
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
        tool_providers=[NativeBuiltinToolProvider(folder, session_key=_SESSION)],
        cwd=folder,
        session_key=_SESSION,
    )
    runtime.set_approval_policy(policy)
    await runtime.start()
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in runtime.stream("go"):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await runtime.approve_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    return seen


def _asked(seen: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in seen if e.kind == EVENT_PERMISSION_REQUEST]


def _result(seen: list[AgentEvent]) -> AgentEvent:
    """The result of the turn's last call."""
    return [e for e in seen if e.kind == EVENT_TOOL_RESULT][-1]


def _read_first(target: Path, raw: str) -> list[tuple[str, dict[str, Any]]]:
    """A read of *target* when it exists, as an agent makes one before it writes a file over."""
    return [("read_file", {"path": raw})] if target.exists() else []


def _refused_as_owner_only(result: AgentEvent, *, policy: str) -> None:
    """The marks of a call the owner-only rule refused, audited as that refusal. Where the policy
    asks, it is refused before the question (the tool's pre-flight); where the policy approves it
    unasked, the tool refuses it as it starts, with the same words and the same rule."""
    assert result.tool_meta.get("ok") is False
    if policy == "":
        assert result.tool_meta.get(TOOL_META_NOT_RUN) == "refused_by_tool"
    assert unasked_outcome(result.tool_meta) == "refused"
    assert unasked_reason(result.tool_meta) == "owner_only"


# ── the native file tools, under every approval mode ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("target", [".git/hooks/pre-commit", ".git/config"])
async def test_a_file_tool_write_to_a_hook_or_the_settings_is_refused_under_every_mode(
    owner, policy, target
):
    """🔴 Before: the write ran (approved by the owner, or by the policy unasked) and the owner's
    own git then ran what it wrote. Now nobody is asked, the agent is told why in the home rule's
    words, and the file is as it was."""
    path = owner.repo / target
    before = path.read_text() if path.exists() else None

    seen = await _native_turn(
        owner.repo,
        *_read_first(path, target),
        ("write_file", {"path": target, "content": "#!/bin/sh\necho hi\n"}),
        policy=policy,
    )

    assert _asked(seen) == [], "nobody is asked about a change only the owner may make"
    result = _result(seen)
    text = str(result.tool_output)
    assert f"Blocked: “{target}” {GITS}" in text and OWNERS in text, text
    assert "Leave git's settings and hook scripts to the owner" in text
    _refused_as_owner_only(result, policy=policy)
    assert (path.read_text() if path.exists() else None) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", POLICIES)
async def test_an_edit_of_the_settings_is_refused_under_every_mode(owner, policy):
    seen = await _native_turn(
        owner.repo,
        ("read_file", {"path": ".git/config"}),
        ("edit_file", {"path": ".git/config", "old_str": "bare = false", "new_str": "bare = true"}),
        policy=policy,
    )

    assert _asked(seen) == []
    _refused_as_owner_only(_result(seen), policy=policy)
    assert (owner.git / "config").read_text() == SETTINGS


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("target", ["CHANGES.md", ".gitignore", ".gitattributes"])
async def test_an_ordinary_file_of_the_repository_is_written_as_before(owner, policy, target):
    """The control: the repository's own files, its ignore and attribute rules among them, are
    the agent's to change, asked about as any write is."""
    seen = await _native_turn(
        owner.repo, ("write_file", {"path": target, "content": "written\n"}), policy=policy
    )

    assert len(_asked(seen)) == (1 if policy == "" else 0)
    assert _result(seen).tool_meta.get("ok") is not False, _result(seen).tool_output
    assert (owner.repo / target).read_text() == "written\n"


# ── one file, however its path is written ───────────────────────────────────────────────────


def _layout(owner) -> dict[str, Path]:
    """The rest of git's files, as git lays them out: a submodule's folder, a linked worktree's
    (with the file that names its folder), a bare repository, a rebase in progress, and a link."""
    git = owner.git
    sub = git / "modules" / "theme"
    for folder in (sub / "hooks", sub / "objects", sub / "refs"):
        folder.mkdir(parents=True)
    (sub / "HEAD").write_text("ref: refs/heads/main\n")
    (sub / "config").write_text(SETTINGS)
    linked = git / "worktrees" / "draft"
    linked.mkdir(parents=True)
    (linked / "HEAD").write_text("ref: refs/heads/draft\n")
    (linked / "commondir").write_text("../..\n")
    draft = owner.workspace / "draft"
    draft.mkdir()
    (draft / ".git").write_text(f"gitdir: {linked}\n")
    bare = owner.workspace / "backup.git"
    for folder in (bare / "hooks", bare / "objects", bare / "refs"):
        folder.mkdir(parents=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "rebase-merge").mkdir()
    (git / "rebase-merge" / "git-rebase-todo").write_text("pick 1234567 notes\n")
    (owner.repo / "hooks-link").symlink_to(git / "hooks")
    return {"draft": draft, "bare": bare}


def _refusal(owner, raw: str):
    """Why the file tools refuse a write to *raw*, from the workspace, or ``None``."""
    from personalclaw.file_scope import refusal

    return refusal("write_file", {"path": raw, "content": "x"}, cwd=owner.workspace)


@pytest.mark.parametrize(
    "raw",
    [
        "notes-site/.git/config",
        "notes-site/.git/hooks/pre-commit",
        "notes-site/.git/hooks",
        "notes-site/.GIT/HOOKS/pre-commit",
        "notes-site/notes/../.git/config",
        "notes-site/hooks-link/post-checkout",
        "{repo}/.git/config",
        "notes-site/.gitmodules",
        "notes-site/.git/modules/theme/config",
        "notes-site/.git/modules/theme/hooks/post-checkout",
        "notes-site/.git/worktrees/draft/commondir",
        "notes-site/.git/worktrees/draft/config.worktree",
        "notes-site/.git/rebase-merge/git-rebase-todo",
        "draft/.git",
        "backup.git/hooks/post-receive",
        "backup.git/config",
    ],
)
def test_every_spelling_of_one_of_gits_files_is_that_file(owner, raw):
    """🔴 Before: each was written. A link, a ``..``, a case the disk does not tell apart, and the
    folders git keeps a submodule's, a linked worktree's and a bare repository's settings and hooks
    in all name the same files, and each is refused."""
    _layout(owner)
    refused = _refusal(owner, raw.format(repo=owner.repo))

    assert refused is not None and refused.control == "owner_only", (raw, refused)
    assert GITS in str(refused) and OWNERS in str(refused)


@pytest.mark.parametrize(
    "raw",
    [
        "notes-site/.git/info/exclude",
        "notes-site/src/hooks/use-notes.ts",
        "notes-site/config/site.yml",
        "notes-site/config",
        "notes-site/docs/.gitkeep",
        "draft/README.md",
        "backup.git/description",
    ],
)
def test_a_file_that_only_shares_a_name_is_not_one(owner, raw):
    """The control: a project's own ``hooks/`` and ``config`` are not git's, nor is what git only
    reads as patterns or text."""
    _layout(owner)
    assert _refusal(owner, raw) is None, raw


def test_a_startup_file_the_file_tools_reach_is_refused(owner):
    """🔴 Before: with the owner's home folder an allowed working directory, ``write_file`` wrote
    her ``~/.zshrc`` and her git settings. Now each is refused for what it is, and the rest of the
    folder is still hers to have the agent change."""
    allowed = {"agent": {"subagent_cwd_allowed_roots": [str(owner.user)]}}
    (owner.home / "config.json").write_text(json.dumps(allowed))
    for raw, words in (("~/.zshrc", SHELLS), ("~/.bashrc", SHELLS), ("~/.gitconfig", GITS)):
        refused = _refusal(owner, raw)
        assert refused is not None and refused.control == "owner_only", (raw, refused)
        assert words in str(refused) and OWNERS in str(refused), str(refused)
    assert _refusal(owner, "~/notes/today.md") is None
    assert (owner.user / ".zshrc").read_text() == STARTUP


# ── an agent CLI's own write, edit and patch ────────────────────────────────────────────────


def _screened(title: str, args: dict[str, Any], *, cwd: Path) -> Any:
    from personalclaw.acp.permission_authority import screen_tool_call

    return screen_tool_call(None, title, json.dumps(args), cwd=cwd)


def _calls(owner) -> dict[str, tuple[str, dict[str, Any]]]:
    """How each agent CLI asks to change ``.git/hooks/pre-commit``: its own write tool by title
    and input, a write tool whose title names nothing, an edit, and a patch in each shape a patch
    tool sends its changes in (by path, as a list, and as a move)."""
    hook = str(owner.git / "hooks" / "pre-commit")
    return {
        "a write": (f"Write {hook}", {"file_path": hook, "content": "#!/bin/sh\n"}),
        "a write titled by its tool": (
            "fs_write",
            {"command": "create", "path": ".git/hooks/pre-commit", "fileText": "#!/bin/sh\n"},
        ),
        "an edit": ("Edit", {"file_path": str(owner.git / "config"), "old_string": "a"}),
        "a patch by path": ("apply_patch", {"changes": {hook: {"add": {"content": "x"}}}}),
        "a patch as a list": (
            "Edit",
            {"changes": [{"path": hook, "kind": {"type": "add"}, "diff": "+x"}]},
        ),
        "a patch that moves a file there": (
            "apply_patch",
            {
                "changes": {
                    str(owner.repo / "README.md"): {
                        "update": {"unified_diff": "", "move_path": hook}
                    }
                }
            },
        ),
        "its shell": (f"Running: printf x > {hook}", {"command": f"printf x > {hook}"}),
    }


@pytest.mark.parametrize(
    "call",
    [
        "a write",
        "a write titled by its tool",
        "an edit",
        "a patch by path",
        "a patch as a list",
        "a patch that moves a file there",
        "its shell",
    ],
)
def test_an_agent_clis_change_to_one_is_refused_where_it_asks(owner, call):
    """🔴 Before: the gate an agent CLI asks read none of these inputs, so the chat's Trust or a
    standing grant approved the change."""
    from personalclaw.hooks import TOOL_DENY

    title, args = _calls(owner)[call]
    verdict = _screened(title, args, cwd=owner.repo)

    assert verdict.action == TOOL_DENY, (call, verdict)
    assert verdict.control == "owner_only"
    assert GITS in verdict.reason and OWNERS in verdict.reason


def test_an_agent_clis_ordinary_change_and_read_still_reach_its_approval(owner):
    """The control: the same tools on the repository's own files, and a read of git's settings,
    are left to the chat's approval as before."""
    from personalclaw.hooks import TOOL_DENY

    readme = str(owner.repo / "README.md")
    ignore = str(owner.repo / ".gitignore")
    for title, args in (
        (f"Write {readme}", {"file_path": readme, "content": "x"}),
        ("apply_patch", {"changes": {ignore: {"add": {"content": "*.log\n"}}}}),
        (f"Reading {owner.git / 'config'}", {"path": str(owner.git / "config")}),
        ("Running: cat .git/config", {"command": "cat .git/config"}),
    ):
        assert _screened(title, args, cwd=owner.repo).action != TOOL_DENY, title


@pytest.mark.asyncio
async def test_in_a_trusted_chat_the_clis_write_to_a_hook_is_refused_and_an_ordinary_one_runs(
    owner, tmp_path
):
    """🔴 Before: with the chat's Trust on, the agent CLI's own write to the hook was approved
    without asking. The chat runner asks the one screen first; the hook chain here allows every
    call, so what refuses it is the reading of the call's input."""
    from test_acp_permission_authority import (
        _context_builder,
        _drive,
        _make_state,
        _session,
        _set_stream,
    )

    from personalclaw.llm.base import EVENT_COMPLETE as DONE
    from personalclaw.llm.base import EVENT_PERMISSION_REQUEST as ASKS
    from personalclaw.llm.base import LLMEvent

    async def asks(title: str, args: dict[str, Any]):
        state, client = _make_state(tmp_path, context_builder=_context_builder())
        session = _session(trust=True)
        _set_stream(
            client,
            [
                LLMEvent(
                    kind=ASKS,
                    title=title,
                    tool_kind="edit",
                    request_id="req-1",
                    tool_input=json.dumps(args),
                ),
                LLMEvent(kind=DONE, stop_reason="end_turn"),
            ],
        )
        rows = MagicMock()
        await _drive(state, session, sel_mock=rows)
        decided = [
            (c.kwargs.get("outcome"), (c.kwargs.get("metadata") or {}).get("decided_by"))
            for c in rows.return_value.log_tool_invocation.call_args_list
            if c.kwargs.get("request_id") == "req-1"
        ]
        return client, decided, json.dumps([m.get("content") for m in session.messages])

    title, args = _calls(owner)["a write"]
    client, decided, chat = await asks(title, args)
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()
    assert decided == [("refused", "owner_only")], decided
    assert "an agent may not change it" in chat

    readme = str(owner.repo / "README.md")
    client, decided, _ = await asks(f"Write {readme}", {"file_path": readme, "content": "x"})
    client.approve_tool.assert_awaited_once_with("req-1")
    assert decided and decided[0][0] == "auto_approved", decided


# ── the agent's shell ───────────────────────────────────────────────────────────────────────


def _bash(owner, command: str, *, mode: str = "off"):
    tools = NativeBuiltinToolProvider(owner.repo, sandbox_mode=mode)
    return asyncio.run(tools.invoke("bash", {"command": command}))


@pytest.mark.parametrize("mode", ["off", "auto"])
@pytest.mark.parametrize(
    "command,named",
    [
        ("printf '#!/bin/sh\\n' > .git/hooks/pre-commit", ".git/hooks/pre-commit"),
        ("chmod +x .git/hooks/pre-commit.sample", ".git/hooks/pre-commit.sample"),
        ("cd .git && echo '[core]' >> config", ".git/config"),
        ("git config core.hooksPath .githooks", ".git/config"),
        ("git config --global core.pager less", "~/.gitconfig"),
        ("git config set --global core.editor vi", "~/.gitconfig"),
        ("git -C {repo} config --unset core.bare", "{repo}/.git/config"),
        ("echo '[submodule \"theme\"]' >> .gitmodules", ".gitmodules"),
        ("echo 'export PATH=$PATH:~/bin' >> ~/.zshrc", "~/.zshrc"),
        ("cp {repo}/README.md ~/.profile", "~/.profile"),
    ],
)
def test_the_shell_does_not_run_a_command_that_changes_one(owner, mode, command, named):
    """🔴 Before: each ran, sandbox on or off. The shell's screen reads every path a command names
    and every file the reading of it establishes it writes (a ``git config`` that sets a value
    writes git's settings), and refuses before anyone is asked, whatever the sandbox does."""
    result = _bash(owner, command.format(repo=owner.repo), mode=mode)

    named = named.format(repo=owner.repo)
    assert not result.success
    assert f"Blocked: “{named}” is where" in result.error and OWNERS in result.error, result.error
    assert (owner.git / "config").read_text() == SETTINGS
    assert not (owner.git / "hooks" / "pre-commit").exists()
    assert (owner.user / ".zshrc").read_text() == STARTUP
    assert not (owner.user / ".gitconfig").exists() and not (owner.repo / ".gitmodules").exists()


@pytest.mark.parametrize(
    "command",
    [
        "cat .git/config",
        "ls .git/hooks",
        "grep -n bare .git/config",
        "printf '.git\\nnode_modules\\n' > .dockerignore",
        "echo '*.log' >> .gitignore",
        "echo hi > notes.md",
        "cat ~/.zshrc",
        ". ~/.zshrc && echo $EDITOR",
    ],
)
def test_the_shell_still_reads_them_and_writes_the_rest(owner, command):
    """The control: reading git's settings and the startup files, running a startup file in the
    agent's own shell, and writing the repository's own files run as before."""
    result = _bash(owner, command)
    assert result.success, result.error


# ── the sandbox: what it holds, on each platform ────────────────────────────────────────────


def test_the_macos_profile_holds_the_owners_own_files_and_not_a_repositorys(owner):
    """The owner's git settings and startup files sit at paths known before the shell starts, so
    the macOS profile denies a write to each, at every level, in both spellings of a link into a
    dotfiles folder, and pins that folder so it cannot be moved aside. A repository's settings and
    hooks are not in it: git writes those itself in a shell's ordinary work."""
    from personalclaw.sandbox import _build_seatbelt_profile

    dotfiles = owner.user / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "bashrc").write_text(STARTUP)
    (owner.user / ".bashrc").symlink_to(dotfiles / "bashrc")
    user = str(owner.user)
    for level in ("standard", "cc", "strict"):
        profile = _build_seatbelt_profile(level)
        for name in (".zshrc", ".bashrc", ".profile", ".gitconfig"):
            assert f'(deny file-write* (literal "{user}/{name}"))' in profile, (level, name)
        real = os.path.realpath(dotfiles / "bashrc")
        assert f'(deny file-write* (literal "{real}"))' in profile
        assert f'(deny file-write* (literal "{os.path.realpath(dotfiles)}"))' in profile
        assert str(owner.git) not in profile and ".git/config" not in profile


def test_the_linux_launcher_holds_none_of_them(owner):
    """On Linux the owner's own files are held by the screen alone: the launcher could hold them
    only by making her whole home folder read-only, and it holds no repository's either."""
    from personalclaw.sandbox import _build_launcher_script

    launcher = _build_launcher_script("standard")
    assert ".zshrc" not in launcher and ".gitconfig" not in launcher
    assert str(owner.repo) not in launcher


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="the macOS profile is driven here; Linux holds these by the screen alone",
)
def test_macos_the_fence_holds_a_startup_file_no_reading_of_the_command_sees(owner):
    """A command that builds the path out of pieces passes every reading of its text, so on macOS
    the kernel is what refuses it. An ordinary file in the home folder is written as before."""
    from personalclaw.sandbox import sandbox_exec_argv

    hidden = (
        "import os; h = os.environ['HOME']; "
        "open(os.path.join(h, '.zs' + 'hrc'), 'a').write('export X=1\\n')"
    )
    ordinary = "import os; open(os.path.join(os.environ['HOME'], 'notes.txt'), 'w').write('x')"
    for code, refused in ((hidden, True), (ordinary, False)):
        argv, profile = sandbox_exec_argv(["/usr/bin/python3", "-c", code], "standard")
        try:
            run = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(profile)
        if refused:
            assert run.returncode != 0 and "Operation not permitted" in run.stderr, run.stderr
        else:
            assert run.returncode == 0, run.stderr
    assert (owner.user / ".zshrc").read_text() == STARTUP
    assert (owner.user / "notes.txt").read_text() == "x"


# ── an automation's files to change ─────────────────────────────────────────────────────────


def test_an_automation_may_not_name_one_as_a_file_it_changes(owner):
    """🔴 Before: ``writes`` took a repository's hook, its settings and the owner's startup file,
    so the run's ``write_file`` was admitted there."""
    from personalclaw.write_scope import problem

    hook = f"{owner.repo}/.git/hooks/pre-commit"
    assert problem([hook]) == (
        f"{hook} is where git keeps what it reads and runs as you, which no automation may change"
    )
    assert "where your shell keeps what it runs as you" in problem(["~/.zshrc"])
    assert problem([f"{owner.repo}/.gitignore", f"{owner.repo}/README.md"]) == ""
