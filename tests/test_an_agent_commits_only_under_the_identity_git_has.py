"""An agent commits only under the identity git is configured with; a command that gives git an
author or committer of its own is put to a person past every grant.

A helper fixing a README committed with an identity it made up,
``git -c user.name='…' -c user.email='<first name>@<the project's domain>' commit -s``, though the
repository and global settings both held the owner's real address. The author, the committer and
the ``Signed-off-by`` line all carried the made-up address, and a sign-off is the developer's
certificate of origin, given in the owner's name. Nothing that reads a command looked at a
per-command identity, so under the helper's grant it ran like any commit.

Now the command reading names each way a command gives git a commit's author or committer itself
(``CommandEffects.identity``): a ``user.*``, ``author.*`` or ``committer.*`` setting in ``git -c``,
``--config-env`` or ``git config``, ``git commit --author``, and the ``GIT_AUTHOR_*``,
``GIT_COMMITTER_*``, ``EMAIL`` and ``GIT_CONFIG_*`` variables, through ``sh -c``, ``env``,
``export`` and ``git rebase --exec``. Every gate puts such a call to a person whatever grant
stands, as it does a delete of a protected folder (``run_bounds.Reach.identity``), and an
unattended run is refused it and told to commit under the configured identity or leave the commit
to the owner. A plain commit follows the normal rules.

Every command here is decided and never run.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from test_an_off_list_host_is_asked_past_every_grant import (
    _answer,
    _asked,
    _background,
    _quiet_sel,
    _shell_ask,
    _state,
)
from test_an_unattended_run_stays_inside_its_bounds import _result
from test_an_unattended_run_stays_inside_its_bounds import _run as _native_turn

from personalclaw import run_bounds
from personalclaw.command_effects import command_effects

#: The helper's own command, as it ran (its name and address stood in for).
MADE_UP = (
    "git -c user.name='Ann Lee' -c user.email='ann@acme.example' commit -s -m 'docs: fix README'"
)
PLAIN = "git commit -s -m 'docs: fix README'"


@pytest.fixture(autouse=True)
def _temporary_folder_of_its_own(tmp_path, monkeypatch):
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(folder))


# ── The reading ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "named"),
    [
        (MADE_UP, {"user.name", "user.email"}),
        ("git -c user.email=x@example.org commit -m m", {"user.email"}),
        ("git -C repo -c User.Email=x@example.org commit -m m", {"user.email"}),
        ("git -c author.email=x@example.org commit -m m", {"author.email"}),
        ("git --config-env=user.email=ADDRESS commit -m m", {"user.email"}),
        ('git commit --author="A <a@example.org>" -m m', {"--author"}),
        ('git commit --auth "A <a@example.org>" -m m', {"--author"}),
        ('git commit --amend --author="A <a@example.org>" --no-edit', {"--author"}),
        ("GIT_AUTHOR_EMAIL=a@example.org git commit -m m", {"GIT_AUTHOR_EMAIL"}),
        ("env GIT_COMMITTER_NAME=A git commit -m m", {"GIT_COMMITTER_NAME"}),
        ("export GIT_AUTHOR_EMAIL=a@example.org; git commit -m m", {"GIT_AUTHOR_EMAIL"}),
        ("git config user.email a@example.org && git commit -m m", {"user.email"}),
        ("git config --global user.name A", {"user.name"}),
        ("git config set user.email a@example.org", {"user.email"}),
        ('sh -c "git -c user.email=a@example.org commit -m m"', {"user.email"}),
        ("cd docs && git -c user.email=a@example.org commit -m m", {"user.email"}),
        ('git rebase --exec "git commit --amend --author=A --no-edit" main', {"--author"}),
        ("git -c user.email=a@example.org commit -F - <<EOF\nm\nEOF", {"user.email"}),
        (
            "printf '[user]\\n\\temail = a@example.org\\n' >> .git/config",
            {"a write to git's settings"},
        ),
        (
            "cat >> ~/.gitconfig <<EOF\n[user]\nemail = a@example.org\nEOF",
            {"a write to git's settings"},
        ),
        ("sed -i s/ann@/a@/ ~/.gitconfig", {"a write to git's settings"}),
        ("cp elsewhere.ini .git/config", {"a write to git's settings"}),
        ("GIT_CONFIG_GLOBAL=other.ini git commit -m m", {"GIT_CONFIG_GLOBAL"}),
        ("HOME=/tmp/other git commit -m m", {"HOME"}),
        ("git -c include.path=/tmp/other.ini commit -m m", {"include.path"}),
        ("git config includeIf.gitdir:~/w/.path /tmp/other.ini", {"include.path"}),
        ("git config --rename-section staged user", {"a section renamed to say who commits"}),
        ("git config --edit", {"git config --edit"}),
        ("git commit -C 1a2b3c", {"--reuse-message"}),
        ("git commit --reuse-message=1a2b3c", {"--reuse-message"}),
        ("git am 0001-fix.patch", {"git am"}),
    ],
    ids=[
        "the helper's own command",
        "-c user.email",
        "a setting in another case, in another folder",
        "-c author.email",
        "--config-env",
        "--author",
        "--author abbreviated",
        "--amend --author",
        "GIT_AUTHOR_EMAIL",
        "env GIT_COMMITTER_NAME",
        "an exported variable",
        "git config, then a commit",
        "git config --global",
        "git config set",
        "through sh -c",
        "after a cd",
        "a rebase's --exec",
        "with a message from a here-document",
        "a user section appended to the repository's settings",
        "a user section appended to the owner's settings",
        "an address edited in the owner's settings",
        "another settings file copied over the repository's",
        "another file for the owner's settings",
        "another home for git",
        "settings included from another file",
        "settings included for a folder",
        "a section renamed into user",
        "the settings edited in an editor",
        "another commit's author reused",
        "another commit's author reused, written long",
        "a patch's author applied",
    ],
)
def test_each_way_a_command_gives_git_its_own_identity_is_named(command, named):
    """🔴 Red before: each read as a plain commit, a write to the repository and nothing more."""
    assert set(command_effects(command).identity) == named


@pytest.mark.parametrize(
    "command",
    [
        PLAIN,
        "git commit --amend --reset-author --no-edit",
        'git commit -m "--author is mentioned here"',
        "git log --author=ann",
        "git config user.email",
        "git config --get user.name",
        "git config --unset user.email",
        "git -c core.pager=cat log",
        "echo GIT_AUTHOR_EMAIL=a@example.org",
        "git commit -C 1a2b3c --reset-author",
        "git cherry-pick 1a2b3c",
        "git am --abort",
        "git config --rename-section staged done",
        "HOME=/tmp/other ls",
        "cat .git/config",
        "grep email ~/.gitconfig",
    ],
)
def test_a_plain_commit_and_a_read_of_the_identity_are_not_put_past_every_grant(command, tmp_path):
    call = _shell_ask(command, native=False)

    assert not run_bounds.put_to_a_person(call, session_key="dashboard:x", cwd=str(tmp_path))


# ── Every gate ───────────────────────────────────────────────────────────────────────────────────


def _chat(tmp_path, **posture):
    from personalclaw.dashboard.state import _ChatSession

    session = _ChatSession("chat-1-commits")
    session.workspace_dir = str(tmp_path)
    for name, value in posture.items():
        setattr(session, name, value)
    return session


def _turn(client, command: str, *, native: bool) -> None:
    from unittest.mock import MagicMock

    from test_an_off_list_host_is_asked_past_every_grant import _events

    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

    client.stream = MagicMock(
        side_effect=lambda *a, **k: _events(
            [
                _shell_ask(command, native=native),
                LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"),
            ]
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False], ids=["the built-in agent", "an agent CLI"])
async def test_trust_does_not_answer_a_commit_under_an_identity_the_agent_gave(tmp_path, native):
    """🔴 Red before: the chat's Trust answered it, as it answers any commit."""
    from personalclaw.dashboard.chat import run_chat

    state, client = _state(tmp_path)
    session = _chat(tmp_path, _trust=True)
    _turn(client, MADE_UP, native=native)
    answering = _answer(session, "rejected")

    with _quiet_sel():
        await run_chat(state, session, "fix the README and commit it")
    await answering

    asked = _asked(session)
    assert len(asked) == 1, "Trust answered a commit under a made-up identity"
    reach = json.loads(asked[0]["cls"])["reach"]
    assert "It gives git the author or committer of a commit itself" in reach
    assert "user.email" in reach and "user.name" in reach
    assert "is always asked about" in reach
    client.approve_tool.assert_not_called()


@pytest.mark.asyncio
async def test_trust_still_answers_a_plain_commit(tmp_path):
    from personalclaw.dashboard.chat import run_chat

    state, client = _state(tmp_path)
    session = _chat(tmp_path, _trust=True)
    _turn(client, PLAIN, native=False)

    with _quiet_sel():
        await run_chat(state, session, "commit it")

    assert _asked(session) == []
    client.approve_tool.assert_called_once()


@pytest.mark.asyncio
async def test_a_helpers_grant_does_not_answer_it():
    """🔴 Red before: the helper's grant answered its commit, and nobody saw the address."""
    client, relayed = await _background(_shell_ask(MADE_UP, native=False))

    relayed.assert_awaited_once()
    client.approve_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_helpers_grant_still_answers_a_plain_commit():
    client, relayed = await _background(_shell_ask(PLAIN, native=False))

    relayed.assert_not_awaited()
    client.approve_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_built_in_agent_asks_though_its_grant_answers_every_other_call(tmp_path):
    from personalclaw.llm.events import EVENT_PERMISSION_REQUEST

    events, invoked, _ = await _native_turn(
        tmp_path, "bash", {"command": MADE_UP}, unattended=False
    )

    asks = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert len(asks) == 1 and invoked == [], "the run's grant answered the commit"


@pytest.mark.asyncio
async def test_an_unattended_run_is_refused_it_and_told_how_to_commit(tmp_path):
    from personalclaw.llm.events import TOOL_META_REFUSED_BY

    events, invoked, _ = await _native_turn(tmp_path, "bash", {"command": MADE_UP}, unattended=True)

    assert invoked == []
    result = _result(events)
    assert result.tool_meta.get(TOOL_META_REFUSED_BY) == "run_bounds"
    assert "it gives git the author or committer of a commit itself" in result.tool_output
    assert "commit with a plain `git commit`" in result.tool_output
    assert "leave the commit for the user" in result.tool_output


@pytest.mark.asyncio
async def test_a_one_shot_with_nobody_to_ask_refuses_it():
    from personalclaw.llm_helpers import ToolApprovalPolicy, _resolve_permission

    provider = AsyncMock()

    approved = await _resolve_permission(
        provider, _shell_ask(MADE_UP, native=True), ToolApprovalPolicy.AUTO_APPROVE, None
    )

    assert approved is False
    provider.reject_tool.assert_awaited_once()


def test_the_cards_line_says_why_it_asks():
    reach = run_bounds.Reach(identity=("--author",))

    note = run_bounds.ask_note(reach)

    assert note.startswith("It gives git the author or committer of a commit itself (--author)")
    assert note.endswith("is always asked about, whatever this chat or its agent allows.")
    assert reach.asks


# ── Every commit a helper made, checked against who git commits as ───────────────────────────────


OWNER = ("Ann Lee", "ann@example.org")


def _git(repo, *args: str, env: dict | None = None) -> str:
    import os
    import subprocess

    # The suite's own identity variables are left out, so a plain commit is made as the
    # repository's settings say, as an agent's is.
    full = {
        **{
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))
        },
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        **(env or {}),
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=full
    ).stdout.strip()


def _repo(tmp_path, *, identity=OWNER):
    repo = tmp_path / "readme-fix"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    if identity:
        _git(repo, "config", "user.name", identity[0])
        _git(repo, "config", "user.email", identity[1])
    else:
        _git(repo, "config", "user.useConfigOnly", "true")
    (repo / "README.md").write_text("old\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "user.name=Setup",
        "-c",
        "user.email=setup@example.org",
        "commit",
        "-qm",
        "start",
    )
    return repo


def _commit(repo, message: str, *extra: str, env: dict | None = None) -> str:
    (repo / "README.md").write_text(message + "\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, *extra, "commit", "-qm", message, env=env)
    return _git(repo, "rev-parse", "--short=9", "HEAD")


def _helper(repo, name="hc0001"):
    from types import SimpleNamespace

    return SimpleNamespace(id=name, cwd=str(repo))


@pytest.mark.asyncio
async def test_a_helpers_commit_under_a_made_up_identity_is_named_in_its_report(tmp_path):
    """🔴 Red before: the report carried the commit as the helper described it, and the chat that
    started it reviewed the trailers and called the commit signed."""
    from personalclaw import helper_commits, subagent_report

    repo = _repo(tmp_path)
    helper = _helper(repo)
    await helper_commits.started(helper)
    plain = _commit(repo, "docs: a plain one", "-c", "commit.gpgsign=false")
    made_up = _commit(
        repo,
        "docs: fix README\n\nSigned-off-by: Ann Lee <ann@acme.example>",
        "-c",
        "user.email=ann@acme.example",
    )
    await helper_commits.ended(helper)

    note = helper_commits.note_for("hc0001")
    assert made_up in note and plain not in note
    assert "author Ann Lee <ann@acme.example>" in note
    assert "signed off by Ann Lee <ann@acme.example>" in note
    assert "Ann Lee <ann@example.org>" in note  # who git commits as there
    assert "do not call them signed" in note
    for said in (
        subagent_report.for_a_person("hc0001", "Fixed the README and committed it, signed off."),
        subagent_report.for_a_model("hc0001", "Fixed the README and committed it, signed off."),
    ):
        assert said.endswith(note)


@pytest.mark.parametrize(
    ("how", "extra", "env"),
    [
        ("--author", ("--author=Bo <bo@example.org>",), None),
        ("a variable", (), {"GIT_COMMITTER_EMAIL": "bot@example.org"}),
    ],
)
@pytest.mark.asyncio
async def test_each_other_author_or_committer_is_named(tmp_path, how, extra, env):
    from personalclaw import helper_commits

    repo = _repo(tmp_path)
    helper = _helper(repo, name=f"hc-{how}")
    await helper_commits.started(helper)
    made = _git(repo, "rev-parse", "--short=9", "HEAD")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "x", *extra, env=env)
    made = _git(repo, "rev-parse", "--short=9", "HEAD")
    await helper_commits.ended(helper)

    assert made in helper_commits.note_for(f"hc-{how}")


@pytest.mark.asyncio
async def test_a_commit_after_the_helper_rewrote_gits_settings_is_still_named(tmp_path):
    """The owner's identity is the one git committed as when the helper started, read before it
    ran anything: a [user] section it wrote into the settings afterwards changes nothing."""
    from personalclaw import helper_commits

    repo = _repo(tmp_path)
    helper = _helper(repo, name="hc0003")
    await helper_commits.started(helper)
    with (repo / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write("[user]\n\temail = ann@acme.example\n")
    made = _commit(repo, "docs: after the settings changed")
    await helper_commits.ended(helper)

    assert made in helper_commits.note_for("hc0003")


@pytest.mark.asyncio
async def test_with_no_identity_configured_every_commit_it_made_is_named(tmp_path):
    from personalclaw import helper_commits

    repo = _repo(tmp_path, identity=None)
    helper = _helper(repo, name="hc0004")
    await helper_commits.started(helper)
    made = _commit(repo, "docs: under any name", "-c", "user.name=A", "-c", "user.email=a@x.org")
    await helper_commits.ended(helper)

    note = helper_commits.note_for("hc0004")
    assert made in note and "git had no identity" in note


@pytest.mark.asyncio
async def test_plain_commits_and_a_helper_that_made_none_carry_no_note(tmp_path):
    from personalclaw import helper_commits, subagent_report

    repo = _repo(tmp_path)
    for name, commits in (("hc0005", 2), ("hc0006", 0)):
        helper = _helper(repo, name=name)
        await helper_commits.started(helper)
        for n in range(commits):
            _commit(repo, f"docs: plain {n}")
        await helper_commits.ended(helper)
        assert helper_commits.note_for(name) == ""
        assert subagent_report.for_a_person(name, "done") == "done"


@pytest.mark.asyncio
async def test_a_helper_run_checks_its_commits_before_its_report_is_delivered(tmp_path):
    """Through the manager's own run: the check reads the folder as the helper starts, and its
    note is there by the time the report is queued for the chat."""
    from unittest.mock import patch

    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw import helper_commits
    from personalclaw.subagent import SubagentInfo, SubagentManager

    repo = _repo(tmp_path)
    made: list[str] = []

    async def _run_inner(info, session_key):
        made.append(_commit(repo, "docs: fix", "-c", "user.email=ann@acme.example"))
        info.result = "Committed, signed off."
        info.done = True

    delivered: list[str] = []
    manager = SubagentManager(
        sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder(), is_yolo=lambda: False
    )
    manager._on_done = lambda *_a, **_k: None
    manager._enqueue_delivery = lambda info: delivered.append(helper_commits.note_for(info.id))
    info = SubagentInfo(id="hc0007", task="fix the README", parent_session_key="", cwd=str(repo))
    with (
        patch.object(manager, "_run_inner", _run_inner),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        await manager._run(info)

    assert len(delivered) == 1 and made[0] in delivered[0]
